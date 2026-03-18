import os
from flask import Flask, redirect, request, render_template, session, url_for
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Warning: Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

@app.route("/")
def index():
    user = session.get("user")
    return render_template("index.html", user=user)

@app.route("/login/twitter")
def login_twitter():
    if not supabase:
        return "Supabase client not configured", 500
    
    # Define the redirect URL for Supabase to return the user to
    redirect_url = url_for("auth_callback", _external=True)
    
    # Use PKCE flow to sign in with OAuth provider
    # Note: 'twitter' is the provider name for X
    auth_response = supabase.auth.sign_in_with_oauth({
        "provider": "twitter",
        "options": {
            "redirect_to": redirect_url
        }
    })
    
    # Supabase returns the OAuth URL to redirect the user to
    return redirect(auth_response.url)

@app.route("/auth/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return "Auth code missing", 400
    
    # Exchange the PKCE code for a session
    try:
        session_data = supabase.auth.exchange_code_for_session({
            "auth_code": code
        })
        
        # Store user info in Flask session
        user = session_data.user
        session["user"] = {
            "id": user.id,
            "email": user.email,
            "name": user.user_metadata.get("full_name", "User"),
            "avatar": user.user_metadata.get("avatar_url")
        }
        
        return redirect(url_for("dashboard"))
    except Exception as e:
        return f"Authentication failed: {str(e)}", 401

@app.route("/dashboard")
def dashboard():
    user = session.get("user")
    if not user:
        return redirect(url_for("index"))
    return render_template("dashboard.html", user=user)

@app.route("/logout")
def logout():
    session.pop("user", None)
    # Note: supabase.auth.sign_out() would clear the server-side session too if desired
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, port=3000)
