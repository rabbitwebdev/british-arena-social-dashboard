"""One-shot social follower/like milestones, isolated per organisation.

A milestone only fires when a published, armed target is crossed during a
successful poll. Publishing a target already met never manufactures an event.
"""
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing

METRICS=("facebook","instagram","facebook_likes")
DEFAULT_MESSAGE="Target reached! Thank you for following us."
ANIMATIONS=("celebrate","confetti","football","fireworks","image_only")

def db_open(path):
    db=sqlite3.connect(str(path),timeout=15)
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init(path):
    with closing(db_open(path)) as db:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS company_target_states(
              org_id TEXT NOT NULL,metric TEXT NOT NULL,
              goal INTEGER NOT NULL, state TEXT NOT NULL,
              event_id TEXT, reached_at INTEGER,
              PRIMARY KEY(org_id,metric),
              FOREIGN KEY(org_id) REFERENCES organisations(id))""")
            cols={row[1] for row in db.execute("PRAGMA table_info(company_target_states)")}
            if "active_index" not in cols:
                db.execute("ALTER TABLE company_target_states ADD COLUMN active_index INTEGER NOT NULL DEFAULT 0")


def _one(settings):
    if not isinstance(settings,dict) or set(settings)-{
        "enabled","goal","message","animation","countdown_enabled",
        "countdown_message","celebration_image","display_duration"}:
        raise ValueError("Invalid milestone settings")
    enabled=settings.get("enabled",False)
    goal=settings.get("goal",0)
    message=settings.get("message",DEFAULT_MESSAGE)
    animation=settings.get("animation","confetti")
    celebration_image=settings.get("celebration_image","")
    display_duration=settings.get("display_duration",8)
    countdown_enabled=settings.get("countdown_enabled",False)
    countdown_message=settings.get("countdown_message","Only {remaining} more to go!")
    if type(enabled) is not bool or type(goal) is not int or goal<0 or goal>2_000_000_000:
        raise ValueError("Invalid milestone target")
    if enabled and goal<1:
        raise ValueError("A milestone needs a target above zero")
    if not isinstance(message,str) or not 1<=len(message.strip())<=240:
        raise ValueError("Milestone text must be 1–240 characters")
    if animation not in ANIMATIONS:
        raise ValueError("Unsupported milestone animation")
    if type(display_duration) is not int or not 5<=display_duration<=3600:
        raise ValueError("Target celebration duration must be between 5 and 3600 seconds")
    if not isinstance(celebration_image,str) or (celebration_image and not re.fullmatch(r"media:[a-f0-9]{32}",celebration_image)):
        raise ValueError("Celebration image must be a company-uploaded image")
    if enabled and animation=="image_only" and not celebration_image:
        raise ValueError("Image only celebrations require an uploaded 16:9 image")
    if type(countdown_enabled) is not bool:
        raise ValueError("Invalid countdown setting")
    if not isinstance(countdown_message,str) or not 1<=len(countdown_message.strip())<=240:
        raise ValueError("Countdown text must be 1–240 characters")
    if countdown_enabled and (not enabled or countdown_message.count("{remaining}")!=1):
        raise ValueError("Enable a milestone and include {remaining} once in its countdown text")
    return {"enabled":enabled,"goal":goal,"message":message.strip(),"animation":animation,
            "countdown_enabled":countdown_enabled,"countdown_message":countdown_message.strip(),
            "celebration_image":celebration_image,"display_duration":display_duration}


def validate(data):
    """Single targets remain unchanged; next_targets adds up to 19 follow-ups."""
    if not isinstance(data,dict) or set(data)-set(METRICS):
        raise ValueError("Invalid milestone settings")
    clean={}
    for metric,raw in data.items():
        if not isinstance(raw,dict):
            raise ValueError("Invalid milestone for "+metric)
        extra=raw.get("next_targets",[])
        if not isinstance(extra,list) or len(extra)>19:
            raise ValueError("Maximum 20 milestones per social channel")
        base=_one({k:v for k,v in raw.items() if k!="next_targets"})
        steps=[base]
        previous=base["goal"]
        for item in extra:
            step=_one(item)
            if not base["enabled"] or not step["enabled"] or step["goal"]<=previous:
                raise ValueError("Milestone targets must be enabled and strictly increasing")
            previous=step["goal"]
            steps.append(step)
        if extra:base["next_targets"]=steps[1:]
        clean[metric]=base
    return clean


def stages(config):
    return [{k:v for k,v in config.items() if k!="next_targets"},
            *config.get("next_targets",[])]

def _goals(config):
    return {key:val for key,val in validate(config.get("targets",{})).items() if val["enabled"]}

def _align(db,org,goals,counts):
    db.execute("DELETE FROM company_target_states WHERE org_id=? AND metric NOT IN ("+
               ",".join("?" for _ in goals)+")",(org,*goals)) if goals else db.execute(
               "DELETE FROM company_target_states WHERE org_id=?",(org,))
    for metric,config in goals.items():
        old=db.execute("SELECT goal FROM company_target_states WHERE org_id=? AND metric=?",
                       (org,metric)).fetchone()
        if old and old[0]==config["goal"]:
            continue
        baseline=counts.get(metric)
        # A newly published target that has already been hit is marked
        # reached without an event. Unknown counts must first establish a
        # baseline, not trigger a fabricated celebration.
        state="waiting" if baseline is None else ("reached" if baseline>=config["goal"] else "armed")
        db.execute("""INSERT INTO company_target_states
            (org_id,metric,goal,state,event_id,reached_at) VALUES (?,?,?,?,NULL,NULL)
            ON CONFLICT(org_id,metric) DO UPDATE SET
            goal=excluded.goal,state=excluded.state,event_id=NULL,reached_at=NULL""",
            (org,metric,config["goal"],state))

def after_publish(db_path,org,config):
    goals=_goals(config)
    with closing(db_open(db_path)) as db:
        with db:
            row=db.execute("SELECT facebook_count,instagram_count,facebook_likes,status FROM company_social_counts WHERE org_id=?",
                           (org,)).fetchone()
            counts=dict(zip(METRICS,row[:3])) if row and row[3]=="connected" else {}
            _align(db,org,goals,counts)

def after_sync(db_path,org,counts):
    """Call only after a successful fresh Graph API sample."""
    with closing(db_open(db_path)) as db:
        with db:
            config_row=db.execute("SELECT published FROM company_screen_config WHERE org_id=?",
                                  (org,)).fetchone()
            if not config_row:return
            goals=_goals(json.loads(config_row[0]))
            _align(db,org,goals,counts)
            for metric,goal in goals.items():
                value=counts.get(metric)
                if value is None:continue
                row=db.execute("SELECT state FROM company_target_states WHERE org_id=? AND metric=?",
                               (org,metric)).fetchone()
                if not row:continue
                if row[0]=="waiting":
                    db.execute("UPDATE company_target_states SET state=? WHERE org_id=? AND metric=?",
                               ("reached" if value>=goal["goal"] else "armed",org,metric))
                elif row[0]=="armed" and value>=goal["goal"]:
                    db.execute("""UPDATE company_target_states SET
                         state='reached',event_id=?,reached_at=?
                         WHERE org_id=? AND metric=? AND state='armed'""",
                         (secrets.token_hex(16),int(time.time()),org,metric))

def events(db_path,org,config):
    goals=_goals(config)
    if not goals:return {}
    with closing(db_open(db_path)) as db:
        rows=db.execute("""SELECT metric,goal,state,event_id,reached_at
                       FROM company_target_states WHERE org_id=?""",(org,)).fetchall()
    result={}
    for metric,goal,state,event_id,reached_at in rows:
        if metric not in goals or goal!=goals[metric]["goal"]:continue
        result[metric]={"goal":goal,"state":state,"event_id":event_id,
                        "reached_at":reached_at,"message":goals[metric]["message"],
                        "animation":goals[metric]["animation"],
                        "celebration_image":goals[metric]["celebration_image"],
                        "display_duration":goals[metric]["display_duration"],
                        "countdown_enabled":goals[metric]["countdown_enabled"],
                        "countdown_message":goals[metric]["countdown_message"]}
    return result
