"""Irreversible platform-admin-only deletion of a tenant and its private media.

Never delete the canonical British Arena organisation. Never delete shared staff
accounts: one login may belong to several companies.
"""
import os
import re
import secrets
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import tenant_media

ORG_ID=re.compile(r"^[a-z0-9-]{1,64}$")
CHILD_TABLES=(
    ("client_invitations","organisation_id"),
    ("tenant_oauth_states","organisation_id"),
    ("tenant_meta_pending","organisation_id"),
    ("tenant_meta_connections","organisation_id"),
    ("company_social_counts","org_id"),
    ("company_screen_keys","org_id"),
    ("company_screen_config","org_id"),
    ("company_media","org_id"),
    ("organisation_members","organisation_id"),
)

def delete_organisation(db_path,org,confirmation):
    """Atomically remove all known tenant rows. Quarantine media before commit.

    Returns counts and whether final disk cleanup succeeded. Database remains
    authoritative; if cleanup fails, the private quarantined files cannot be
    retrieved through media endpoints and can be removed by an operator.
    """
    if org=="british-arena":
        raise PermissionError("The original British Arena company is protected")
    if not isinstance(org,str) or not ORG_ID.fullmatch(org):
        raise ValueError("Invalid company")
    if not isinstance(confirmation,dict) or confirmation.get("id")!=org:
        raise ValueError("Company ID confirmation does not match")
    root=tenant_media.media_root(db_path)
    directory=root/org
    trash=root/(".delete-"+secrets.token_hex(16))
    moved=False
    count={}
    with closing(sqlite3.connect(str(db_path),timeout=20)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT name FROM organisations WHERE id=?",(org,)).fetchone()
            if row is None:
                raise ValueError("Company does not exist")
            name=row[0]
            if confirmation.get("name")!=name or confirmation.get("phrase")!="DELETE "+name:
                raise ValueError("Type DELETE followed by the exact company name")
            if directory.is_symlink():
                raise ValueError("Unsafe company media directory")
            if directory.exists():
                if not directory.is_dir():raise ValueError("Invalid company media directory")
                # Rename within the same mounted volume before deleting rows.
                # On SQL rollback, restore the media directory.
                directory.rename(trash)
                moved=True
            for table,key in CHILD_TABLES:
                count[table]=db.execute(
                    "DELETE FROM "+table+" WHERE "+key+"=?",(org,)).rowcount
            db.execute("DELETE FROM organisations WHERE id=?",(org,))
            db.commit()
        except Exception:
            db.rollback()
            if moved:
                trash.rename(directory)
            raise
    cleaned=True
    if moved:
        try:shutil.rmtree(trash)
        except OSError:cleaned=False
    return {"deleted":True,"company":name,"id":org,
            "records_deleted":sum(count.values()),"media_cleaned":cleaned}

def route(handler,db_path,method):
    parts=handler.path.split("?",1)[0].strip("/").split("/")
    if len(parts)!=4 or parts[:2]!=["api","organisations"] or parts[3]!="delete":
        return False
    if method!="POST":
        handler.json_response(405,{"error":"method not allowed"});return True
    user=handler.current_user()
    if not user:
        handler.json_response(401,{"error":"sign in required"});return True
    # This is an operator action, not a client self-service action.
    if not handler.is_admin() or not handler.same_origin():
        handler.json_response(403,{"error":"platform administrator only"});return True
    org=parts[2]
    if org=="british-arena":
        handler.json_response(403,{"error":"The original British Arena company cannot be deleted"});return True
    try:
        import json
        data=json.loads(handler.post_body(4096) or b"{}")
        handler.json_response(200,delete_organisation(db_path,org,data))
    except (ValueError,TypeError,sqlite3.IntegrityError):
        handler.json_response(400,{"error":"Company not deleted. Verify its name and ID."})
    except (OSError,sqlite3.OperationalError):
        handler.json_response(503,{"error":"Unable to delete company safely. Nothing was removed."})
    return True
