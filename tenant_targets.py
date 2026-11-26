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
        "countdown_message","celebration_image","display_duration",
        "timer_enabled","timer_message"}:
        raise ValueError("Invalid milestone settings")
    enabled=settings.get("enabled",False)
    goal=settings.get("goal",0)
    message=settings.get("message",DEFAULT_MESSAGE)
    animation=settings.get("animation","confetti")
    celebration_image=settings.get("celebration_image","")
    display_duration=settings.get("display_duration",8)
    countdown_enabled=settings.get("countdown_enabled",False)
    countdown_message=settings.get("countdown_message","Only {remaining} more to go!")
    timer_enabled=settings.get("timer_enabled",False)
    timer_message=settings.get("timer_message","Offer ends in {seconds}s")
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
    if type(timer_enabled) is not bool:
        raise ValueError("Invalid celebration timer visibility")
    if not isinstance(timer_message,str) or not 1<=len(timer_message.strip())<=160:
        raise ValueError("Celebration timer text must be 1–160 characters")
    if timer_enabled and timer_message.count("{seconds}")!=1:
        raise ValueError("Celebration timer text must include {seconds} exactly once")
    if type(countdown_enabled) is not bool:
        raise ValueError("Invalid countdown setting")
    if not isinstance(countdown_message,str) or not 1<=len(countdown_message.strip())<=240:
        raise ValueError("Countdown text must be 1–240 characters")
    if countdown_enabled and (not enabled or countdown_message.count("{remaining}")!=1):
        raise ValueError("Enable a milestone and include {remaining} once in its countdown text")
    return {"enabled":enabled,"goal":goal,"message":message.strip(),"animation":animation,
            "countdown_enabled":countdown_enabled,"countdown_message":countdown_message.strip(),
            "celebration_image":celebration_image,"display_duration":display_duration,
            "timer_enabled":timer_enabled,"timer_message":timer_message.strip()}


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
    if goals:
        db.execute("DELETE FROM company_target_states WHERE org_id=? AND metric NOT IN ("+
                   ",".join("?" for _ in goals)+")",(org,*goals))
    else:
        db.execute("DELETE FROM company_target_states WHERE org_id=?",(org,))
    for metric,config in goals.items():
        steps=stages(config)
        row=db.execute("""SELECT goal,state,active_index FROM company_target_states
                          WHERE org_id=? AND metric=?""",(org,metric)).fetchone()
        baseline=counts.get(metric)
        initial="waiting" if baseline is None else (
            "reached" if baseline>=steps[0]["goal"] else "armed")
        if row:
            goal,state,index=row
            if index<len(steps) and steps[index]["goal"]==goal:
                continue
            # An appended milestone can resume a previously finished sequence.
            if state=="complete" and 0<index<len(steps) and steps[index-1]["goal"]==goal:
                next_goal=steps[index]["goal"]
                next_state="waiting" if baseline is None else (
                    "reached" if baseline>=next_goal else "armed")
                db.execute("""UPDATE company_target_states SET goal=?,state=?,event_id=NULL,
                              reached_at=NULL WHERE org_id=? AND metric=?""",
                           (next_goal,next_state,org,metric))
                continue
            if state=="complete" and index==len(steps) and steps[-1]["goal"]==goal:
                continue
        db.execute("""INSERT INTO company_target_states
          (org_id,metric,goal,state,event_id,reached_at,active_index)
          VALUES (?,?,?,?,NULL,NULL,0)
          ON CONFLICT(org_id,metric) DO UPDATE SET
          goal=excluded.goal,state=excluded.state,event_id=NULL,reached_at=NULL,active_index=0""",
          (org,metric,steps[0]["goal"],initial))

def after_publish(db_path,org,config):
    goals=_goals(config)
    with closing(db_open(db_path)) as db:
        with db:
            row=db.execute("SELECT facebook_count,instagram_count,facebook_likes,status FROM company_social_counts WHERE org_id=?",
                           (org,)).fetchone()
            counts=dict(zip(METRICS,row[:3])) if row and row[3]=="connected" else {}
            _align(db,org,goals,counts)

def _advance_due(db,org,metric,config,now,counts):
    steps=stages(config)
    row=db.execute("""SELECT active_index,state,reached_at,event_id FROM company_target_states
                      WHERE org_id=? AND metric=?""",(org,metric)).fetchone()
    if not row:return
    index,state,reached_at,event_id=row
    # Targets already exceeded before they were published have no celebration.
    # Skip these silently when a following target exists.
    if state=="reached" and reached_at is None and event_id is None and len(steps)>1:
        value=counts.get(metric)
        if value is not None:
            next_index=next((i for i in range(index+1,len(steps))
                              if steps[i]["goal"]>value),len(steps))
            if next_index==len(steps):
                db.execute("""UPDATE company_target_states SET active_index=?,goal=?,
                  state='complete',event_id=NULL,reached_at=NULL WHERE org_id=? AND metric=?""",
                  (len(steps),steps[-1]["goal"],org,metric))
            else:
                db.execute("""UPDATE company_target_states SET active_index=?,goal=?,
                  state='armed',event_id=NULL,reached_at=NULL WHERE org_id=? AND metric=?""",
                  (next_index,steps[next_index]["goal"],org,metric))
        return
    if state!="reached" or reached_at is None or now<reached_at+steps[index]["display_duration"]:
        return
    next_index=index+1
    if next_index==len(steps):
        db.execute("""UPDATE company_target_states SET active_index=?,state='complete',
                  event_id=NULL,reached_at=NULL WHERE org_id=? AND metric=?""",
                   (next_index,org,metric))
    else:
        goal=steps[next_index]["goal"]
        # The new goal becomes armed after the last image/animation duration.
        # Only the next successful Meta sample may fire it.
        state="armed" if counts.get(metric) is not None else "waiting"
        db.execute("""UPDATE company_target_states SET active_index=?,goal=?,state=?,
                  event_id=NULL,reached_at=NULL WHERE org_id=? AND metric=?""",
                   (next_index,goal,state,org,metric))


def after_sync(db_path,org,counts):
    """Process a fresh Graph sample; never trigger the next goal during the prior display."""
    with closing(db_open(db_path)) as db:
        with db:
            config_row=db.execute("SELECT published FROM company_screen_config WHERE org_id=?",
                                  (org,)).fetchone()
            if not config_row:return
            goals=_goals(json.loads(config_row[0]))
            now=int(time.time())
            _align(db,org,goals,counts)
            for metric,config in goals.items():
                _advance_due(db,org,metric,config,now,counts)
                value=counts.get(metric)
                if value is None:continue
                row=db.execute("""SELECT state,active_index FROM company_target_states
                                  WHERE org_id=? AND metric=?""",(org,metric)).fetchone()
                if not row:continue
                state,index=row
                if state=="complete":continue
                current=stages(config)[index]
                if state=="waiting":
                    db.execute("UPDATE company_target_states SET state=? WHERE org_id=? AND metric=?",
                               ("reached" if value>=current["goal"] else "armed",org,metric))
                elif state=="armed" and value>=current["goal"]:
                    db.execute("""UPDATE company_target_states SET
                           state='reached',event_id=?,reached_at=?
                           WHERE org_id=? AND metric=? AND state='armed'""",
                           (secrets.token_hex(16),now,org,metric))


def events(db_path,org,config):
    goals=_goals(config)
    if not goals:return {}
    with closing(db_open(db_path)) as db:
        with db:
            sample=db.execute("""SELECT facebook_count,instagram_count,facebook_likes,status
                     FROM company_social_counts WHERE org_id=?""",(org,)).fetchone()
            counts=dict(zip(METRICS,sample[:3])) if sample and sample[3]=="connected" else {}
            _align(db,org,goals,counts)
            now=int(time.time())
            for metric,config in goals.items():
                _advance_due(db,org,metric,config,now,counts)
            rows=db.execute("""SELECT metric,goal,state,event_id,reached_at,active_index
                        FROM company_target_states WHERE org_id=?""",(org,)).fetchall()
    result={}
    for metric,goal,state,event_id,reached_at,index in rows:
        if metric not in goals:continue
        steps=stages(goals[metric])
        item=steps[-1] if state=="complete" else steps[index]
        result[metric]={"goal":goal,"state":state,"event_id":event_id,
                        "reached_at":reached_at,"message":item["message"],
                        "animation":item["animation"],
                        "celebration_image":item["celebration_image"],
                        "display_duration":item["display_duration"],
                        "countdown_enabled":item["countdown_enabled"],
                        "countdown_message":item["countdown_message"],
                        "timer_enabled":item["timer_enabled"],
                        "timer_message":item["timer_message"],
                        "active_index":index,"total_targets":len(steps)}
    return result
