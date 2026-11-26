"""Company-owned image uploads for published TV screens.

Files live on Railway's existing persistent /data volume, never in Git.
Only validated JPEG/PNG/WebP raster images are accepted and re-encoded.
"""
import io
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

import tenant_meta

MAX_BYTES=8*1024*1024
MAX_PIXELS=20_000_000
MAX_FILES=100
MAX_TOTAL=150*1024*1024
IMAGE_ID=re.compile(r"^[a-f0-9]{32}$")
ORG_ID=re.compile(r"^[a-z0-9-]{1,64}$")
IMAGE_FORMATS={"JPEG","PNG","WEBP"}
IMAGE_TYPES={"image/jpeg","image/png","image/webp"}
MEDIA_REF=re.compile(r"^media:([a-f0-9]{32})$")

Image.MAX_IMAGE_PIXELS=MAX_PIXELS

def db_open(db_path):
    db=sqlite3.connect(str(db_path),timeout=20)
    db.execute("PRAGMA foreign_keys=ON")
    return db

def media_root(db_path):
    return Path(db_path).resolve().parent/"company-media"

def init(db_path):
    with closing(db_open(db_path)) as db:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS company_media(
                id TEXT PRIMARY KEY, org_id TEXT NOT NULL, size INTEGER NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL,
                uploaded_at INTEGER NOT NULL,
                FOREIGN KEY(org_id) REFERENCES organisations(id))""")
            db.execute("CREATE INDEX IF NOT EXISTS company_media_org ON company_media(org_id)")

def media_id(value):
    if not isinstance(value,str):return None
    match=MEDIA_REF.fullmatch(value)
    return match.group(1) if match else None

def _path(db_path,org,identifier):
    if not ORG_ID.fullmatch(org) or not IMAGE_ID.fullmatch(identifier):
        raise ValueError("Invalid media reference")
    return media_root(db_path)/org/(identifier+".webp")

def exists(db_path,org,identifier):
    if not IMAGE_ID.fullmatch(identifier):return False
    with closing(db_open(db_path)) as db:
        row=db.execute("SELECT 1 FROM company_media WHERE id=? AND org_id=?",
                       (identifier,org)).fetchone()
    return bool(row and _path(db_path,org,identifier).is_file())

def validate_settings(db_path,org,settings):
    images=[settings.get("logo","")]+[s.get("image","") for s in settings.get("slides",[])]
    targets=settings.get("targets") or {}
    target_steps=[]
    for target in targets.values():
        target_steps.append(target)
        target_steps.extend(target.get("next_targets",[]))
    images.extend(t.get("celebration_image","") for t in target_steps)
    # Celebration art is uploaded 16:9 only, not an externally hosted URL.
    for target in target_steps:
        ref=target.get("celebration_image","")
        if not ref:continue
        ident=media_id(ref)
        if not ident:raise ValueError("Upload a 16:9 celebration image")
        with closing(db_open(db_path)) as db:
            row=db.execute("SELECT width,height FROM company_media WHERE id=? AND org_id=?",
                           (ident,org)).fetchone()
        if not row or not exists(db_path,org,ident):
            raise ValueError("Celebration image is not in this company's uploads")
        width,height=row
        if not height or abs(width/height-16/9)>0.035:
            raise ValueError("Celebration image must use a 16:9 aspect ratio")
    for ref in images:
        if isinstance(ref,str) and ref.startswith("media:"):
            ident=media_id(ref)
            if not ident or not exists(db_path,org,ident):
                raise ValueError("Image is not in this company's uploads")

def image_bytes(payload,content_type):
    if content_type.split(";",1)[0].strip().lower() not in IMAGE_TYPES:
        raise ValueError("Upload a PNG, JPG or WebP image")
    if not payload or len(payload)>MAX_BYTES:
        raise ValueError("Image must be under 8 MB")
    try:
        with Image.open(io.BytesIO(payload)) as original:
            if original.format not in IMAGE_FORMATS:
                raise ValueError("Unsupported image file")
            if original.width*original.height>MAX_PIXELS:
                raise ValueError("Image dimensions too large")
            original.load()
            image=ImageOps.exif_transpose(original)
            # Strip EXIF, profiles and other metadata on re-encoding.
            image=image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((2560,2560),Image.Resampling.LANCZOS)
            output=io.BytesIO()
            image.save(output,format="WEBP",quality=86,method=4)
            result=output.getvalue()
            if len(result)>MAX_BYTES:
                raise ValueError("Converted image exceeds 8 MB")
            return result,image.width,image.height
    except (UnidentifiedImageError,Image.DecompressionBombError,OSError,OverflowError) as exc:
        raise ValueError("Invalid or damaged image") from exc

def upload(db_path,org,payload,content_type):
    encoded,width,height=image_bytes(payload,content_type)
    identifier=secrets.token_hex(16)
    path=_path(db_path,org,identifier)
    with closing(db_open(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        count,total=db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM company_media WHERE org_id=?",
                               (org,)).fetchone()
        if count>=MAX_FILES or total+len(encoded)>MAX_TOTAL:
            db.rollback();raise ValueError("Company image storage limit reached")
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        try:
            # Exclusive create; never overwrite uploaded company media.
            with path.open("xb") as out:out.write(encoded)
            db.execute("""INSERT INTO company_media(id,org_id,size,width,height,uploaded_at)
                          VALUES (?,?,?,?,?,?)""",
                       (identifier,org,len(encoded),width,height,int(time.time())))
            db.commit()
        except Exception:
            db.rollback()
            path.unlink(missing_ok=True)
            raise
    return {"reference":"media:"+identifier,"width":width,"height":height,
            "size":len(encoded)}

def referenced_in_published(db_path,org,identifier):
    # Draft uploads must not become public before Publish is clicked.
    import tenant_screens
    settings=tenant_screens.get_config(db_path,org,True)["settings"]
    return identifier in [
        media_id(settings.get("logo","")),
        *[media_id(slide.get("image","")) for slide in settings.get("slides",[])],
        *[media_id(target.get("celebration_image",""))
          for root in settings.get("targets",{}).values()
          for target in [root,*root.get("next_targets",[])]]
    ]

def serve(handler,db_path,org,identifier):
    if not exists(db_path,org,identifier):
        handler.json_response(404,{"error":"image not found"})
        return
    content=_path(db_path,org,identifier).read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type","image/webp")
    handler.send_header("X-Content-Type-Options","nosniff")
    handler.send_header("Cache-Control","private, max-age=60")
    handler.send_header("Referrer-Policy","no-referrer")
    handler.send_header("Content-Length",str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)

def route(handler,db_path,method):
    path=handler.path.split("?",1)[0]
    parts=path.strip("/").split("/")
    # Private opaque TV link, published images only.
    if (len(parts)==5 and parts[:2]==["api","screens"] and parts[3]=="media"
            and method=="GET"):
        import tenant_screens
        org=tenant_screens.screen_org(db_path,parts[2])
        if not org or not IMAGE_ID.fullmatch(parts[4]) or not referenced_in_published(db_path,org,parts[4]):
            handler.json_response(404,{"error":"image not found"});return True
        serve(handler,db_path,org,parts[4])
        return True
    # Private company editor images and raw-byte upload.
    if len(parts)!=5 or parts[:2]!=["api","organisations"] or parts[3]!="media":
        return False
    org=parts[2]
    user=handler.current_user()
    if not user:
        handler.json_response(401,{"error":"sign in required"});return True
    role=tenant_meta.member_role(db_path,org,user["username"])
    if not role:
        handler.json_response(403,{"error":"not a company member"});return True
    if method=="GET":
        if not IMAGE_ID.fullmatch(parts[4]):
            handler.json_response(404,{"error":"image not found"});return True
        serve(handler,db_path,org,parts[4]);return True
    if method=="POST" and parts[4]=="upload":
        if role not in ("owner","manager") or not handler.same_origin():
            handler.json_response(403,{"error":"permission denied"});return True
        try:
            content_type=handler.headers.get("Content-Type","")
            if content_type.split(";",1)[0].strip().lower() not in IMAGE_TYPES:
                raise ValueError("Upload a PNG, JPG or WebP image")
            payload=handler.post_body(MAX_BYTES)
            if payload is None:raise ValueError("Image too large or missing")
            handler.json_response(201,upload(db_path,org,payload,content_type))
        except ValueError as exc:
            handler.json_response(400,{"error":str(exc)})
        return True
    handler.json_response(405,{"error":"method not allowed"});return True
