"""
J.A.R.V.I.S  -  Web App version
================================
A Flask backend that:
  - serves the JARVIS HUD web page
  - receives text commands from the page
  - runs skills (open apps, volume, screenshots, system stats...) on YOUR laptop
  - falls back to the local Ollama AI brain for conversation
  - sends the spoken reply text back to the page (the page speaks it + animates)

The browser handles MIC INPUT (speech-to-text) and VOICE OUTPUT, so the bars
react to real audio. Python does the real laptop actions. Best of both worlds.

SETUP (run once in your terminal):
    pip install flask psutil pyautogui ollama
    (Ollama must be installed and:  ollama pull llama3.2:1b )

RUN:
    python app.py
Then open the address it prints (http://127.0.0.1:5000) in your browser.
"""

import os
import re
import datetime
import webbrowser
import subprocess
import urllib.parse

import psutil
import pyautogui
from flask import Flask, render_template, request, jsonify

try:
    import ollama
    HAS_OLLAMA = True
except Exception:
    HAS_OLLAMA = False

app = Flask(__name__)

# ---- REMINDERS: list of {"time": datetime, "text": str, "fired": bool} ----
REMINDERS = []

USER_NAME = "sir"
LOCAL_MODEL = "llama3.2:1b"
SYSTEM_PROMPT = (
    f"You are JARVIS, a witty AI assistant in the style of Tony Stark's assistant. "
    f"You address the user as '{USER_NAME}'. Formal, dryly humorous, efficient. "
    f"Keep replies to one or two short sentences; they are spoken aloud. No lists, no markdown."
)
history = [{"role": "system", "content": SYSTEM_PROMPT}]

# ---- YOUR CONTACTS: put real numbers with country code, e.g. "+919876543210" ----
# Fill these in yourself. Leave as-is and JARVIS will tell you the number is not set.

CONTACTS = {
    "dad":               "+919962001332",
    "papa":              "+919962001332",
    "mom":               "+918754509065",
    "amma":              "+918754509065",
    "prasanna":          "+918428578888",
    "professor richard": "+919965667345",
    "pellington anna":   "+919566980442",
}

WEBSITES = {
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "google meet": "https://meet.google.com", "meet": "https://meet.google.com",
    "gmail": "https://mail.google.com", "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com", "github": "https://github.com",
    "google": "https://www.google.com",
}
APPS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "file explorer": "explorer.exe", "chrome": "chrome.exe", "paint": "mspaint.exe",
    "settings": "start ms-settings:", "camera": "start microsoft.windows.camera:",
}
CLOSE_APPS = {
    "notepad": "notepad.exe", "calculator": "CalculatorApp.exe", "paint": "mspaint.exe",
    "settings": "SystemSettings.exe",
    "word": "WINWORD.EXE", "excel": "EXCEL.EXE", "spotify": "Spotify.exe",
}


# ---- read the real Windows master volume (0-100), FRESH every call ----
def get_volume():
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        vol = devices.EndpointVolume
        return int(round(vol.GetMasterVolumeLevelScalar() * 100))
    except Exception:
        # fallback for older pycaw
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(interface, POINTER(IAudioEndpointVolume))
            return int(round(vol.GetMasterVolumeLevelScalar() * 100))
        except Exception:
            return None

def _parse_time(low):
    """Parse a clock time..."""
    now = datetime.datetime.now()
    low = low.replace("p.m.", "pm").replace("a.m.", "am").replace("p.m", "pm").replace("a.m", "am")
    # match  H:MM optionally followed by am/pm,  OR  H followed by am/pm
    m = re.search(r"\b(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)?\b", low)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        ampm = m.group(3)
    else:
        m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", low)
        if not m:
            return None
        hh, mm = int(m.group(1)), 0
        ampm = m.group(2)
    # apply am/pm
    if ampm == "pm" and hh != 12:
        hh += 12
    elif ampm == "am" and hh == 12:
        hh = 0
    if hh > 23 or mm > 59:
        return None
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:                      # time already passed today -> tomorrow
        target += datetime.timedelta(days=1)
    return target


def run_skill(text):
    """Return a spoken reply if a skill matches, else None."""
    low = text.lower()
    # --- SHUT DOWN / RESTART / SLEEP the laptop (with safety cancel) ---
    if ("shut down" in low or "shutdown" in low or "turn off" in low) and "cancel" not in low:
        subprocess.run("shutdown /s /t 20", shell=True)
        return f"Shutting down in 20 seconds, {USER_NAME}. Say 'cancel shutdown' to stop."
    if "restart" in low or "reboot" in low:
        subprocess.run("shutdown /r /t 20", shell=True)
        return f"Restarting in 20 seconds, {USER_NAME}. Say 'cancel shutdown' to stop."
    if "cancel" in low and ("shutdown" in low or "shut down" in low or "restart" in low):
        subprocess.run("shutdown /a", shell=True)
        return f"Shutdown cancelled, {USER_NAME}."
    if "sleep" in low and "laptop" in low or "go to sleep" in low:
        subprocess.run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
        return f"Going to sleep, {USER_NAME}."
    # --- REMINDERS: "remind me about the meeting at 2:45" / "remind me to call dad at 6 pm" ---
    if "remind" in low:
        when = _parse_time(low)
        if when is None:
            return f"When should I remind you, {USER_NAME}? Try 'remind me about the meeting at 2:45 PM'."
        what = low
        for cut in ("remind me to", "remind me about", "remind me", "reminder to", "reminder"):
            if cut in what:
                what = what.split(cut, 1)[1]
                break
        what = re.split(r"\bat\b|\bby\b", what)[0].strip()
        if not what:
            what = "your reminder"
        REMINDERS.append({"time": when, "text": what, "fired": False})
        tstr = when.strftime("%I:%M %p").lstrip("0")
        return f"Reminder set for {tstr}, {USER_NAME}. I'll remind you about {what}."

    # --- list pending reminders ---
    if ("what" in low or "list" in low or "any" in low or "my" in low) and "reminder" in low:
        pending = [r for r in REMINDERS if not r["fired"]]
        if not pending:
            return f"You have no pending reminders, {USER_NAME}."
        parts = [f"{r['text']} at {r['time'].strftime('%I:%M %p').lstrip('0')}" for r in pending]
        return f"You have {len(pending)} reminder, {USER_NAME}: " + "; ".join(parts) + "."

    if "time" in low:
        return f"It is {datetime.datetime.now().strftime('%I:%M %p')}, {USER_NAME}."
    if "date" in low or "what day" in low:
        return f"Today is {datetime.datetime.now().strftime('%A, %d %B %Y')}, {USER_NAME}."
    if "battery" in low:
        b = psutil.sensors_battery()
        if b:
            return f"Battery at {int(b.percent)} percent, {USER_NAME}, {'charging' if b.power_plugged else 'on battery'}."
        return f"No battery detected, {USER_NAME}."
    if "cpu" in low or "processor" in low:
        return f"CPU at {int(psutil.cpu_percent(interval=0.5))} percent, {USER_NAME}."
    if "memory" in low or "ram" in low:
        return f"Memory at {int(psutil.virtual_memory().percent)} percent, {USER_NAME}."

    # report the REAL current volume (no AI guessing)
    if "volume" in low and any(w in low for w in ("what", "current", "level", "how much", "percentage", "how loud")):
        v = get_volume()
        if v is None:
            return f"I can't read the volume level, {USER_NAME}."
        return f"The volume is at {v} percent, {USER_NAME}."

    if "volume" in low and (
            "up" in low or "increase" in low or "raise" in low or "down" in low or "decrease" in low or "reduce" in low or "lower" in low):
        cur = get_volume()  # real current volume, 0-100
        if cur is None:
            return f"I can't read the volume, {USER_NAME}."
        m = re.search(r"\d+", low)
        amount = int(m.group()) if m else 10  # how much to change
        going_down = ("down" in low or "decrease" in low or "reduce" in low or "lower" in low)
        target = cur - amount if going_down else cur + amount
        target = max(0, min(100, target))  # clamp 0-100
        try:
            from pycaw.pycaw import AudioUtilities
            dev = AudioUtilities.GetSpeakers()
            dev.EndpointVolume.SetMasterVolumeLevelScalar(target / 100.0, None)
            direction = "down" if going_down else "up"
            return f"Volume {direction} to {target} percent, {USER_NAME}."
        except Exception:
            return f"I couldn't change the volume, {USER_NAME}."
    if "mute" in low:
        pyautogui.press("volumemute"); return f"Muted, {USER_NAME}."

    if "screenshot" in low or "screen shot" in low:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        path = os.path.join(desktop, f"screenshot_{datetime.datetime.now():%H%M%S}.png")
        pyautogui.screenshot(path)
        return f"Screenshot saved to your Desktop, {USER_NAME}."

    if low.startswith("search for ") or low.startswith("search "):
        q = low.split("search", 1)[1].replace("for", "", 1).strip()
        if q:
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(q))
            return f"Searching for {q}, {USER_NAME}."
    if low.startswith("play "):
        q = text[5:].strip()
        if q:
            webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
            return f"Searching YouTube for {q}, {USER_NAME}."

    # --- WhatsApp MESSAGE (B): "message papa hello" / "jarvis message mom hi" / "whatsapp dad hey" ---
    if ("message" in low or "whatsapp" in low or "text" in low) and "call" not in low:
        # find which contact is mentioned
        target = None
        for name in CONTACTS:
            if name in low:
                target = name; break
        if target:
            number = CONTACTS[target]
            if number.endswith("0000000000"):
                return f"The number for {target} isn't set yet, {USER_NAME}."
            # text = everything AFTER the contact name
            msg = low.split(target, 1)[1].strip()
            if not msg:
                return f"What should I say to {target}, {USER_NAME}?"
            # RELIABLE approach: open WhatsApp Web straight to the chat with the
            # message pre-typed in the box, ready for you to hit Enter. This always
            # works (unlike pywhatkit's blind auto-send which is flaky).
            try:
                import pywhatkit
                pywhatkit.sendwhatmsg_instantly(number, msg, wait_time=15, tab_close=False)
                return f"Message sent to {target}, {USER_NAME}."
            except Exception:
                return f"The message to {target} may not have gone through, {USER_NAME}. WhatsApp Web can be slow."

    # --- WhatsApp CALL (A): "call papa" / "jarvis call mom" -> opens chat ready to call ---
    if "call" in low:
        target = None
        for name in CONTACTS:
            if name in low:
                target = name; break
        if target:
            number = CONTACTS[target]
            if number.endswith("0000000000"):
                return f"The number for {target} isn't set yet, {USER_NAME}."
            num = number.replace("+", "").replace(" ", "")
            webbrowser.open(f"https://web.whatsapp.com/send?phone={num}")
            return f"Opening {target}'s chat, {USER_NAME}. Tap the call button to ring them."

    if "open" in low or "go to" in low:
        if "downloads" in low:
            subprocess.Popen(f'explorer "{os.path.join(os.path.expanduser("~"),"Downloads")}"'); return f"Opening Downloads, {USER_NAME}."
        for name, url in WEBSITES.items():
            if name in low:
                webbrowser.open(url); return f"Opening {name}, {USER_NAME}."
        for name, exe in APPS.items():
            if name in low:
                subprocess.Popen(exe, shell=True); return f"Opening {name}, {USER_NAME}."

    if "close" in low and any(w in low for w in ("browser", "chrome", "tabs")):
        subprocess.Popen("taskkill /F /IM chrome.exe", shell=True); return f"Closing the browser, {USER_NAME}."
    if "close" in low:
        # SAFETY: never close explorer.exe -- it runs the taskbar & desktop
        if "explorer" in low or "file explorer" in low:
            return f"I won't close File Explorer, {USER_NAME} -- that would remove your taskbar and desktop."
        for name, exe in CLOSE_APPS.items():
            if name in low and exe.lower() != "explorer.exe":
                subprocess.Popen(f"taskkill /F /IM {exe}", shell=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Closing {name}, {USER_NAME}."

    if "lock" in low and any(w in low for w in ("computer", "screen", "pc")):
        subprocess.Popen("rundll32.exe user32.dll,LockWorkStation"); return f"Locking the system, {USER_NAME}."

    return None


def ai_brain(text):
    if not HAS_OLLAMA:
        return f"My brain is offline, {USER_NAME}. Please start Ollama."
    history.append({"role": "user", "content": text})
    trimmed = [history[0]] + history[-6:]
    try:
        resp = ollama.chat(model=LOCAL_MODEL, messages=trimmed,
                           options={"num_predict": 80, "temperature": 0.7})
        reply = resp["message"]["content"].strip()
    except Exception as e:
        reply = f"I couldn't reach my brain, {USER_NAME}."
    history.append({"role": "assistant", "content": reply})
    return reply


# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/command", methods=["POST"])
def command():
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify({"reply": ""})
    reply = run_skill(text)
    if reply is None:
        reply = ai_brain(text)
    return jsonify({"reply": reply})


@app.route("/stats")
def stats():
    b = psutil.sensors_battery()
    vol = get_volume()
    # check if any reminder is due now
    now = datetime.datetime.now()
    due_text = ""
    for r in REMINDERS:
        if not r["fired"] and now >= r["time"]:
            r["fired"] = True
            due_text = f"Sir, this is your reminder about {r['text']}."
            break
    return jsonify({
        "cpu": int(psutil.cpu_percent()),
        "mem": int(psutil.virtual_memory().percent),
        "pwr": int(b.percent) if b else 100,
        "vol": vol if vol is not None else -1,
        "reminder": due_text,
    })


if __name__ == "__main__":
    print("=" * 50)
    print("  JARVIS web app starting...")
    print("  Open this in your browser:  http://127.0.0.1:5000")
    print("=" * 50)
    webbrowser.open("http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
