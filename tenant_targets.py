"""One-shot social follower/like milestones, isolated per organisation.

A milestone only fires when a published, armed target is crossed during a
successful poll. Publishing a target already met never manufactures an event.
"""
import json
import secrets
import sqlite3
import time
from contextlib import closing

METRICS=("facebook","instagram","facebook_likes")
DEFAULT_MESSAGE="Target reached! Thank you for following us."
ANIMATIONS=("celebrate","confetti","football","fireworks")

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

def validate(data):
    if not isinstance(data,dict) or set(data)-set(METRICS):
        raise ValueError("Invalid milestone settings")
    clean={}
    for metric,settings in data.items():
        if not isinstance(settings,dict) or set(settings)-{"enabled","goal","message","animation"}:
            raise ValueError("Invalid milestone for "+metric)
        enabled=settings.get("enabled",False)
        goal=settings.get("goal",0)
        message=settings.get("message",DEFAULT_MESSAGE)
        animation=settings.get("animation","confetti")
        if type(enabled) is not bool or type(goal) is not int or goal<0 or goal>2_000_000_000:
            raise ValueError("Invalid milestone target")
        if enabled and goal<1:
            raise ValueError("A milestone needs a target above zero")
        if not isinstance(message,str) or not 1<=len(message.strip())<=240:
            raise ValueError("Milestone text must be 1–240 characters")
        if animation not in ANIMATIONS:
            raise ValueError("Unsupported milestone animation")
        clean[metric]={"enabled":enabled,"goal":goal,"message":message.strip(),"animation":animation}
    return clean

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
                        "animation":goals[metric]["animation"]}
    return result
