import os
import logging
from flask import Flask, redirect, request, render_template, session, url_for, jsonify
from supabase import create_client, Client
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-at-least-32-chars")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
# SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("❌ CRITICAL: Missing SUPABASE_URL or SUPABASE_ANON_KEY in .env")

# Initialize Supabase Client
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None
except Exception as e:
    logger.error(f"❌ Failed to initialize Supabase: {str(e)}")
    supabase = None

@app.route("/")
def index():
    user = session.get("user")
    posts = []
    if supabase:
        try:
            # Execute the specified query: select * from public.staking_posts where live = true order by id desc limit 1 offset 1;
            response = supabase.table("staking_posts") \
                .select("*") \
                .order("id", desc=True) \
                .limit(10) \
                .offset(0) \
                .execute()
            posts = response.data
            print(posts)
        except Exception as e:
            logger.error(f"Error fetching posts: {str(e)}")
            
    return render_template("index.html", user=user, posts=posts)

@app.route("/login/twitter")
def login_twitter():
    if not supabase:
        return "Supabase client not configured. Check your .env file.", 500
    
    # The callback URI that Supabase redirects back to after X login
    # For X OAuth 2.0, ensure this is in your X Redirection whitelist
    redirect_url = url_for("auth_callback", _external=True)
    logger.info(f"Initiating login, redirect_url: {redirect_url}")
    
    try:
        # Use PKCE flow to sign in with OAuth provider
        # Note: 'twitter' is the provider name for X even for OAuth 2.0
        # Adding scopes can help with OAuth 2.0 requirements
        auth_response = supabase.auth.sign_in_with_oauth({
            "provider": "x",
            "options": {
                "redirect_to": redirect_url,
                "scopes": "tweet.read users.read offline.access" # Valid for X OAuth 2.0
            }
        })
        
        if not auth_response or not auth_response.url:
            logger.error(f"Failed to get OAuth URL from Supabase. Response: {auth_response}")
            return "Failed to initiate login. Make sure Twitter/X is enabled in Supabase Dashboard.", 500
            
        return redirect(auth_response.url)
    except Exception as e:
        logger.error(f"Error in login_twitter: {str(e)}")
        return f"Authentication initiation failed: {str(e)}", 500

@app.route("/auth/callback")
def auth_callback():
    code = request.args.get("code")
    error = request.args.get("error")
    error_desc = request.args.get("error_description")
    
    if error:
        logger.error(f"Auth callback error: {error} - {error_desc}")
        return f"Authentication failed: {error_desc}", 401
        
    if not code:
        logger.warning("Auth code missing from callback URL")
        return "Auth code missing from redirect", 400
    
    try:
        # Exchange the code for a session
        session_data = supabase.auth.exchange_code_for_session({
            "auth_code": code
        })
        
        # Store user info in Flask session
        user = session_data.user
        session["user"] = {
            "id": user.id,
            "email": user.email,
            "name": user.user_metadata.get("full_name") or user.user_metadata.get("name") or "User",
            "avatar": user.user_metadata.get("avatar_url"),
            "username": user.user_metadata.get("user_name") # X handle
        }
        
        logger.info(f"User {session['user']['name']} successfully logged in.")
        return redirect(url_for("dashboard"))
    except Exception as e:
        logger.error(f"Failed to exchange code for session: {str(e)}")
        return f"Authentication failed during session exchange: {str(e)}", 401

@app.route("/dashboard")
def dashboard():
    user = session.get("user")
    if not user:
        return redirect(url_for("index"))
    return render_template("dashboard.html", user=user)

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, port=3000)
