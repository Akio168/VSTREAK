import os
import sqlite3
import requests
from google import genai
from google.genai import types
from flask import Flask, flash, redirect, render_template, request, session, jsonify
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta

# --- Application Configuration ---
app = Flask(__name__)
app.secret_key = os.urandom(24) # Generates a random key for signing session cookies
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem" # Stores session data in the flask_session folder
Session(app)

DATABASE = "vstreak.db"

# --- Gamification Logic ---
# Defines XP thresholds, the numeric level, and the Title for the UI
LEVELS = [
    (0,     1, "Rookie"),
    (100,   2, "Grinder"),
    (250,   3, "Pro"),
    (500,   4, "Veteran"),
    (1000, 5, "All-Star"),
    (2000, 6, "Legend"),
    (3500, 7, "GOAT"),
    (5500, 8, "Mythic"),
]

# --- UI Customization ---
# List of available categories for the dropdown menu
CATEGORIES = ["General", "Study", "Coding", "Fitness", "Work", "Personal"]

# Maps categories to Tailwind CSS classes for dynamic pill-badge coloring
CATEGORY_COLORS = {
    "General":  "bg-slate-500",
    "Study":    "bg-blue-500",
    "Coding":   "bg-violet-500",
    "Fitness":  "bg-green-500",
    "Work":     "bg-orange-500",
    "Personal": "bg-pink-500",
}

# --- Database Management ---

def get_db():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect(DATABASE)
    # Allows accessing columns by name (e.g., row['username']) instead of index
    conn.row_factory = sqlite3.Row 
    return conn


def init_db():
    """Initializes the database schema and handles migrations (adding columns to old DBs)."""
    conn = get_db()
    c = conn.cursor()
    
    # Create the core Users table for authentication and gamification
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            hash TEXT NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1
        )
    """)
    
    # Migration Check: Ensures existing users from older versions get XP and Level columns
    for col in ["xp", "level"]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER NOT NULL DEFAULT {'0' if col == 'xp' else '1'}")
        except Exception:
            pass # Column already exists, skip error

    # Create Tasks table with the new Category column
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_name TEXT NOT NULL,
            is_completed INTEGER NOT NULL DEFAULT 0,
            date_created DATE NOT NULL DEFAULT CURRENT_DATE,
            category TEXT NOT NULL DEFAULT 'General'
        )
    """)
    
    # Migration Check: Adds Category column to the Tasks table if it was missing
    try:
        c.execute("ALTER TABLE tasks ADD COLUMN category TEXT NOT NULL DEFAULT 'General'")
    except Exception:
        pass

    # Create History table to track daily "streaks"
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date_won DATE NOT NULL DEFAULT CURRENT_DATE
        )
    """)
    
    # Create Settings table (This was for the 'Bring Your Own Key' model we discussed)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            gemini_api_key TEXT DEFAULT ''
        )
    """)
    
    conn.commit()
    conn.close()


def db_query(sql, args=(), one=False):
    """Helper function to run SELECT queries safely and return results."""
    conn = get_db()
    c = conn.cursor()
    c.execute(sql, args) # Uses parameterized queries to prevent SQL Injection
    rv = c.fetchall()
    conn.close()
    
    # If 'one' is True, returns just the first row; otherwise, returns the whole list
    return (rv[0] if rv else None) if one else rv


# --- Database Execution ---

def db_execute(sql, args=()):
    """
    Handles INSERT, UPDATE, and DELETE operations.
    Returns the ID of the last row inserted (useful for redirecting to new items).
    """
    conn = get_db()
    c = conn.cursor()
    c.execute(sql, args)
    last_id = c.lastrowid # Grabs the ID of the new record just created
    conn.commit() # Saves the changes to the .db file
    conn.close()
    return last_id


# --- Gamification Engine ---

def get_level_info(xp):
    """
    Calculates current level, title, and progress percentage toward the next level.
    This logic powers the progress bar in your UI.
    """
    level_num = 1
    title = "Rookie"
    
    # Loop through the LEVELS list to find where the user's XP currently sits
    for threshold, lvl, name in LEVELS:
        if xp >= threshold:
            level_num = lvl
            title = name
        else:
            break
            
    # Find the next XP threshold to calculate progress percentage
    next_levels = [l for l in LEVELS if l[0] > xp]
    if next_levels:
        next_threshold = next_levels[0][0]
        prev_threshold = LEVELS[level_num - 1][0]
        # Calculate percentage: (Current XP - Floor) / (Ceiling - Floor)
        progress = int(((xp - prev_threshold) / (next_threshold - prev_threshold)) * 100)
    else:
        # User has reached the max level (Mythic)
        next_threshold = xp
        progress = 100
        
    return {
        "level": level_num, 
        "title": title, 
        "xp": xp, 
        "next_threshold": next_threshold, 
        "progress": progress
    }


# --- Middleware ---

@app.after_request
def after_request(response):
    """
    Security measure: Prevents the browser from caching sensitive pages.
    Ensures that if a user logs out, clicking 'Back' doesn't show their dashboard.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


# --- Main Dashboard Route ---

@app.route("/")
def index():
    """
    The main dashboard. Gathers tasks, streaks, leveling data, 
    and chart data to render index.html.
    """
    # 1. Authentication Check
    if not session.get("user_id"):
        return redirect("/login")

    user_id = session["user_id"]
    today = datetime.now().date()

    # 2. Fetch Daily Tasks
    tasks = db_query(
        "SELECT * FROM tasks WHERE user_id = ? AND date_created = ?",
        (user_id, today.strftime("%Y-%m-%d"))
    )

    # 3. Calculate Streak/Win Stats
    wins = db_query(
        "SELECT COUNT(*) as total FROM history WHERE user_id = ?",
        (user_id,), one=True
    )["total"]

    # 4. Prepare Heatmap Data (Last 30 Days)
    history_rows = db_query("SELECT date_won FROM history WHERE user_id = ?", (user_id,))
    won_dates = [row["date_won"] for row in history_rows]
    last_30_days = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]

    # 5. Get User Profile & Level
    user = db_query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    level_info = get_level_info(user["xp"])

    # 6. Prepare Chart.js Data (Last 7 Days)
    week_labels = [] # e.g., ["Mon", "Tue", "Wed"...]
    week_counts = [] # e.g., [5, 2, 8...]
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        week_labels.append((today - timedelta(days=i)).strftime("%a"))
        count = db_query(
            "SELECT COUNT(*) as c FROM tasks WHERE user_id = ? AND date_created = ? AND is_completed = 1",
            (user_id, d), one=True
        )["c"]
        week_counts.append(count)

    # 7. AI Settings & Results
    settings = db_query("SELECT * FROM user_settings WHERE user_id = ?", (user_id,), one=True)
    has_gemini_key = settings and settings["gemini_api_key"]
    # Retrieve AI coach result from session if it exists, then clear it
    ai_result = session.pop("ai_result", None)

    # 8. Render the View
    return render_template(
        "index.html",
        tasks=tasks,
        wins=wins,
        won_dates=won_dates,
        last_30_days=last_30_days,
        level_info=level_info,
        week_labels=week_labels,
        week_counts=week_counts,
        has_gemini_key=has_gemini_key,
        ai_result=ai_result,
        categories=CATEGORIES,
        category_colors=CATEGORY_COLORS,
        username=user["username"],
    )

# --- Task Management Routes ---

@app.route("/add", methods=["POST"])
def add():
    """Adds a new task to the database with a specific name and category."""
    if not session.get("user_id"):
        return redirect("/login")
    
    # Get form data and clean up whitespace
    task_name = request.form.get("task_name", "").strip()
    category = request.form.get("category", "General")
    
    # Validation: Ensure category is valid, default to 'General' if tampered with
    if category not in CATEGORIES:
        category = "General"
    
    if task_name:
        db_execute(
            "INSERT INTO tasks (user_id, task_name, category) VALUES (?, ?, ?)",
            (session["user_id"], task_name, category)
        )
    return redirect("/")


@app.route("/complete", methods=["POST"])
def complete():
    """
    The 'Heart' of VSTREAK: Marks a task done, awards XP, 
    checks for level-ups, and identifies 'Streak Wins'.
    """
    if not session.get("user_id"):
        return redirect("/login")

    task_id = request.form.get("task_id")
    user_id = session["user_id"]
    today = datetime.now().date().strftime("%Y-%m-%d")

    # 1. Check if any tasks were already done today (for first-task flash message)
    already_done = db_query(
        "SELECT COUNT(*) as c FROM tasks WHERE user_id = ? AND date_created = ? AND is_completed = 1",
        (user_id, today), one=True
    )["c"]

    # 2. Update the specific task status
    db_execute(
        "UPDATE tasks SET is_completed = 1 WHERE id = ? AND user_id = ?",
        (task_id, user_id)
    )

    if already_done == 0:
        flash("first_task") # Triggers UI celebration for the first task of the day

    # 3. Gamification: Award 10 XP for every task completed
    db_execute("UPDATE users SET xp = xp + 10 WHERE id = ?", (user_id,))

    # 4. Level-Up Logic: Re-calculate level based on new XP total
    user = db_query("SELECT xp FROM users WHERE id = ?", (user_id,), one=True)
    new_level = get_level_info(user["xp"])["level"]
    db_execute("UPDATE users SET level = ? WHERE id = ?", (new_level, user_id))

    # 5. Streak Logic: Check if ALL tasks for today are now finished
    unfinished = db_query(
        "SELECT * FROM tasks WHERE user_id = ? AND date_created = ? AND is_completed = 0",
        (user_id, today)
    )
    
    if len(unfinished) == 0:
        # Check if they haven't already won today (prevents double XP for winning twice)
        won_today = db_query(
            "SELECT * FROM history WHERE user_id = ? AND date_won = ?",
            (user_id, today)
        )
        if len(won_today) == 0:
            db_execute("INSERT INTO history (user_id) VALUES (?)", (user_id,))
            db_execute("UPDATE users SET xp = xp + 50 WHERE id = ?", (user_id,)) # Bonus XP for daily win
            flash("streak_won")

    return redirect("/")


@app.route("/tasks")
def tasks():
    """View all tasks (past and present) in a list format."""
    if not session.get("user_id"):
        return redirect("/login")

    user_id = session["user_id"]
    all_tasks = db_query(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY date_created DESC, id DESC",
        (user_id,)
    )
    user = db_query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    level_info = get_level_info(user["xp"])

    return render_template(
        "tasks.html",
        tasks=all_tasks,
        level_info=level_info,
        category_colors=CATEGORY_COLORS,
        username=user["username"],
    )


@app.route("/delete", methods=["POST"])
def delete():
    """Permanently removes a task. Restricted to the task owner's ID for security."""
    if not session.get("user_id"):
        return redirect("/login")
    task_id = request.form.get("task_id")
    db_execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, session["user_id"]))
    return redirect("/tasks")


# --- User Authentication ---

@app.route("/login", methods=["GET", "POST"])
def login():
    """Handles user login and password verification."""
    session.clear() # Clear any existing session to start fresh
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if not username:
            return render_template("login.html", error="Must provide username")
            
        row = db_query("SELECT * FROM users WHERE username = ?", (username,), one=True)
        
        # Security: Use check_password_hash to verify against the stored hash
        if not row or not check_password_hash(row["hash"], password):
            return render_template("login.html", error="Invalid username and/or password")
            
        session["user_id"] = row["id"] # Store ID in session to keep user logged in
        return redirect("/")
        
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Handles new user creation with secure password hashing."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if not username or not password:
            return render_template("register.html", error="Must provide username and password")
            
        # Ensure the username isn't already taken
        existing = db_query("SELECT id FROM users WHERE username = ?", (username,), one=True)
        if existing:
            return render_template("register.html", error="Username already taken")
            
        # Security: Never store passwords in plain text! Hash them first.
        hashed = generate_password_hash(password)
        db_execute("INSERT INTO users (username, hash) VALUES (?, ?)", (username, hashed))
        return redirect("/login")
        
    return render_template("register.html")


@app.route("/logout")
def logout():
    """Clears the session and sends the user back to the landing/login page."""
    session.clear()
    return redirect("/")

# --- Gemini API Key Management ---

@app.route("/save-gemini-key", methods=["POST"])
def save_gemini_key():
    """
    Saves or updates the user's personal Gemini API key in the database.
    (Note: In the SaaS model we discussed, this logic is replaced by the .env file).
    """
    if not session.get("user_id"):
        return redirect("/login")
        
    key = request.form.get("gemini_api_key", "").strip()
    user_id = session["user_id"]
    
    # Upsert logic: Update if exists, otherwise Insert
    existing = db_query("SELECT id FROM user_settings WHERE user_id = ?", (user_id,), one=True)
    if existing:
        db_execute("UPDATE user_settings SET gemini_api_key = ? WHERE user_id = ?", (key, user_id))
    else:
        db_execute("INSERT INTO user_settings (user_id, gemini_api_key) VALUES (?, ?)", (user_id, key))
    
    flash("gemini_key_saved")
    return redirect("/")


# --- AI Productivity Coach Logic ---

@app.route("/ai-coach", methods=["POST"])
def ai_coach():
    """
    The 'Analyze My Week' feature. Gathers the last 7 days of tasks
    and sends them to Gemini to generate a coaching summary.
    """
    if not session.get("user_id"):
        return redirect("/login")

    user_id = session["user_id"]
    
    # 1. Verification: Ensure the user actually has a key saved
    settings = db_query("SELECT gemini_api_key FROM user_settings WHERE user_id = ?", (user_id,), one=True)
    if not settings or not settings["gemini_api_key"]:
        flash("no_gemini_key")
        return redirect("/")

    api_key = settings["gemini_api_key"]
    today = datetime.now().date()
    week_data = []

    # 2. Data Gathering: Query the database for the last 7 days of performance
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i))
        d_str = d.strftime("%Y-%m-%d")
        
        # Get names of tasks finished vs. tasks ignored for each day
        completed = db_query(
            "SELECT task_name FROM tasks WHERE user_id = ? AND date_created = ? AND is_completed = 1",
            (user_id, d_str)
        )
        incomplete = db_query(
            "SELECT task_name FROM tasks WHERE user_id = ? AND date_created = ? AND is_completed = 0",
            (user_id, d_str)
        )
        
        week_data.append({
            "date": d.strftime("%A, %b %d"),
            "completed": [r["task_name"] for r in completed],
            "incomplete": [r["task_name"] for r in incomplete],
        })

    # 3. Prompt Engineering: Create the instructions for the AI
    prompt = ("You are an upbeat, elite productivity coach. Analyze this user's last 7 days of task data "
              "and give a SHORT (3-4 sentences max), personalized, encouraging summary. "
              "End with ONE specific, actionable tip. Data follows:\n\n")
    
    for day in week_data:
        prompt += f"**{day['date']}**: Completed: {', '.join(day['completed']) or 'none'}. Incomplete: {', '.join(day['incomplete']) or 'none'}.\n"

    # 4. External API Call: Use requests to talk to Google's Generative Language API
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=15
        )
        resp.raise_for_status() # Trigger an error if the status code isn't 200
        data = resp.json()
        
        # Extract the text content from the nested Gemini JSON response
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        session["ai_result"] = text # Store the result in the session to show on next page load
        
    except Exception as e:
        session["ai_result"] = f"Error reaching Gemini API: {str(e)}"

    return redirect("/")


# --- Chat Interface Route ---

@app.route("/chat")
def chat():
    """Renders the chat page and loads the existing conversation history."""
    if not session.get("user_id"):
        return redirect("/login")
        
    user_id = session["user_id"]
    
    # Get user profile info for the sidebar/navbar
    user = db_query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    level_info = get_level_info(user["xp"])
    
    # Fetch all past messages in chronological order (oldest to newest)
    history = db_query(
        "SELECT sender, message, timestamp FROM chat_messages WHERE user_id = ? ORDER BY timestamp ASC",
        (user_id,)
    )
    
    return render_template(
        "chat.html", 
        chat_history=history, 
        level_info=level_info, 
        username=user["username"]
    )

# --- Advanced AI Chat Interface ---

@app.route("/chat/send", methods=["POST"])
def chat_send():
    """
    The core AI endpoint. It saves the user's message, builds a massive
    context block from the database, and gets a response from Gemini.
    """
    if not session.get("user_id"):
        return jsonify({"error": "Not logged in"}), 401

    # 1. Configuration & Input Validation
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "AI Coach is not configured."}), 500

    data = request.get_json()
    user_message = (data or {}).get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    user_id = session["user_id"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 2. Save the user's message to the DB for conversation history
    db_execute(
        "INSERT INTO chat_messages (user_id, sender, message, timestamp) VALUES (?, 'user', ?, ?)",
        (user_id, user_message, now)
    )

    # 3. Context Gathering: Collect 7 days of task history to "inform" the AI
    today = datetime.now().date()
    recent_tasks = []
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i))
        d_str = d.strftime("%Y-%m-%d")
        done = db_query(
            "SELECT task_name, category FROM tasks WHERE user_id=? AND date_created=? AND is_completed=1",
            (user_id, d_str)
        )
        pending = db_query(
            "SELECT task_name, category FROM tasks WHERE user_id=? AND date_created=? AND is_completed=0",
            (user_id, d_str)
        )
        if done or pending:
            recent_tasks.append({
                "date": d.strftime("%A %b %d"),
                "done": [f"{r['task_name']} ({r['category']})" for r in done],
                "pending": [f"{r['task_name']} ({r['category']})" for r in pending],
            })

    # 4. User Stats Gathering: Get XP, Level, and "Perfect Days"
    total_wins = db_query("SELECT COUNT(*) as c FROM history WHERE user_id=?", (user_id,), one=True)["c"]
    user_row = db_query("SELECT xp, level FROM users WHERE id=?", (user_id,), one=True)
    level_info = get_level_info(user_row["xp"])

    # 5. System Prompt Engineering: Define the AI's "Personality" and Context
    context_block = f"""You are an elite, upbeat AI Productivity Coach embedded in VSTREAK...
    The user currently has {total_wins} perfect days won, {user_row['xp']} XP, and is Level {level_info['level']}.
    """
    for day in recent_tasks:
        context_block += f"\n{day['date']}: Completed: {', '.join(day['done'])} | Pending: {', '.join(day['pending'])}"

    # 6. History Formatting: Prepare past chat logs for the Gemini Chat Session
    past_messages = db_query(
        "SELECT sender, message FROM chat_messages WHERE user_id=? ORDER BY timestamp ASC LIMIT 40",
        (user_id,)
    )

    try:
        client = genai.Client(api_key=api_key)
        history_for_gemini = []
        for msg in past_messages:
            # Map 'user' and 'ai' roles to Google's 'user' and 'model' requirements
            role = "user" if msg["sender"] == "user" else "model"
            history_for_gemini.append(
                types.Content(role=role, parts=[types.Part(text=msg["message"])])
            )

        # 7. Start Chat Session & Get Response
        chat_session = client.chats.create(
            model="gemini-2.5-flash", 
            config=types.GenerateContentConfig(system_instruction=context_block),
            history=history_for_gemini,
        )
        response = chat_session.send_message(user_message)
        ai_text = response.text.strip()
    except Exception as e:
        ai_text = f"Sorry, I ran into an issue: {str(e)}"

    # 8. Save AI Response to Database
    ai_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_execute(
        "INSERT INTO chat_messages (user_id, sender, message, timestamp) VALUES (?, 'ai', ?, ?)",
        (user_id, ai_text, ai_now)
    )

    return jsonify({"reply": ai_text})


# --- Utility Endpoints ---

@app.route("/chat/clear", methods=["POST"])
def chat_clear():
    """Wipes the user's chat history for a fresh start."""
    if not session.get("user_id"):
        return jsonify({"error": "Not logged in"}), 401
    db_execute("DELETE FROM chat_messages WHERE user_id=?", (session["user_id"],))
    return jsonify({"ok": True})


@app.route("/tasks-for-day/<date>")
def tasks_for_day(date):
    """
    Returns task data as JSON for a specific date. 
    Powers the interactivity of the Productivity Heatmap.
    """
    if not session.get("user_id"):
        return jsonify({"error": "Not logged in"}), 401
    completed = db_query(
        "SELECT task_name, category FROM tasks WHERE user_id=? AND date_created=? AND is_completed=1 ORDER BY id",
        (session["user_id"], date)
    )
    incomplete = db_query(
        "SELECT task_name, category FROM tasks WHERE user_id=? AND date_created=? AND is_completed=0 ORDER BY id",
        (session["user_id"], date)
    )
    return jsonify({
        "date": date,
        "completed": [{"name": r["task_name"], "category": r["category"]} for r in completed],
        "incomplete": [{"name": r["task_name"], "category": r["category"]} for r in incomplete],
    })


# --- App Launch ---

# Create tables if they don't exist yet
init_db()

if __name__ == "__main__":
    # Runs the server on port 5000 with Debug Mode active for development
    app.run(host="0.0.0.0", port=5000, debug=True)