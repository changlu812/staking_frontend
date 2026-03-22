import os
import logging
import time
import markdown
from flask import Flask, redirect, request, render_template, session, url_for, jsonify
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions
from dotenv import load_dotenv

MESSAGE_TTL = 300  # 5 minutes

def get_or_create_user(address):
    address = address.lower()
    
    # Reset Postgrest auth to service role to avoid session pollution from Twitter login
    # service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    # if service_key:
    #     supabase.postgrest.auth(service_key)
        
    try:
        response = supabase.table("wallets").select("*").eq("wallet_address", address).execute()
        if response.data:
            supabase.table("wallets").update({"last_login": "now()"}).eq("wallet_address", address).execute()
            return response.data[0], False
        
        new_user = supabase.auth.admin.create_user({
            "email": f"{address}@wallet.local",
            "password": os.urandom(32).hex(),
            "email_confirm": True,
            "user_metadata": {"wallet_address": address}
        })
        user_id = new_user.user.id
        
        wallet_record = supabase.table("wallets").insert({
            "user_id": user_id,
            "wallet_address": address
        }).execute()
        
        return wallet_record.data[0], True
    except Exception as e:
        logger.error(f"❌ CRITICAL Error in get_or_create_user for address {address}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise e


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-at-least-32-chars")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("❌ CRITICAL: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")

try:
    supabase: Client = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
        options=SyncClientOptions(auto_refresh_token=False, persist_session=False)
    ) if SUPABASE_URL else None
except Exception as e:
    logger.error(f"❌ Failed to initialize Supabase: {str(e)}")
    supabase = None

@app.before_request
def reset_supabase_auth():
    """
    Ensure the global supabase client is reset to service_role state 
    before each request to avoid session pollution between users.
    """
    if supabase and SUPABASE_KEY:
        supabase.postgrest.auth(SUPABASE_KEY)



@app.route("/api/auth/wallet/verify", methods=["POST"])
def verify_wallet():
    data = request.get_json()
    address = data.get("address", "").lower()
    signature = data.get("signature", "")
    timestamp = data.get("timestamp", 0)
    
    if not address or not signature:
        return jsonify({"error": "Missing required fields"}), 400
    
    if abs(time.time() - timestamp) > MESSAGE_TTL:
        return jsonify({"error": "Request expired"}), 401
    
    try:
        user, is_new = get_or_create_user(address)
        
        session["user"] = {
            "id": user["user_id"],
            "address": user["wallet_address"],
            "name": f"{user['wallet_address'][:6]}...{user['wallet_address'][-4:]}",
            "login_type": "wallet",
            "is_staked": user.get("is_staked", False),
            "created_at": user.get("created_at")
        }
        
        return jsonify({
            "success": True,
            "is_new_user": is_new,
            "user": {
                "address": user["wallet_address"],
                "created_at": user.get("created_at"),
                "is_staked": user.get("is_staked", False)
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/post/<int:post_id>/live", methods=["POST"])
def make_post_live(post_id):
    user = session.get("user")
    if not user or user.get("login_type") != "wallet":
        return jsonify({"error": "Unauthorized"}), 403

    if supabase:
        try:
            # Verify ownership
            response = supabase.table("staking_posts").select("*").eq("id", post_id).eq("delete", False).single().execute()
            post = response.data
            if not post:
                return jsonify({"error": "POST_NOT_FOUND"}), 404
                
            if post.get("user") != user["id"] and post.get("author_id") != user["id"]:
                return jsonify({"error": "UNAUTHORIZED_ACCESS"}), 403
                
            # Perform update
            supabase.table("staking_posts").update({"live": True}).eq("id", post_id).execute()
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error making post {post_id} live: {str(e)}")
            return jsonify({"error": str(e)}), 500
            
    return jsonify({"success": True})


@app.route("/stake", methods=["GET"])
def stake():
    user = session.get("user")
    if not user or user.get("login_type") != "wallet":
        return redirect(url_for("index"))
    
    return render_template("stake.html", user=user)


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
def edit_post(post_id):
    user = session.get("user")
    if not user:
        return redirect(url_for("index"))
        
    post = None
    if supabase:
        try:
            response = supabase.table("staking_posts") \
                .select("*") \
                .eq("id", post_id) \
                .eq("delete", False) \
                .single() \
                .execute()
            post = response.data
        except Exception as e:
            logger.error(f"Error fetching post {post_id} for edit: {str(e)}")
            
    if not post:
        return "DECRYPT_ERROR: DATA_PACKET_NOT_FOUND", 404
        
    # Check if the user is the author
    if user.get("id") != post.get("user") and user.get("id") != post.get("author_id"):
        return "UNAUTHORIZED_ACCESS: KEY_MISMATCH", 403
        
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        
        if not title or not content:
            return "Missing title or content", 400
            
        if supabase:
            try:
                supabase.table("staking_posts") \
                    .update({"title": title, "content": content}) \
                    .eq("id", post_id) \
                    .execute()
                return redirect(url_for("post_detail", post_id=post_id))
            except Exception as e:
                logger.error(f"Error updating post {post_id}: {str(e)}")
                return f"Error: {str(e)}", 500
                
        return redirect(url_for("post_detail", post_id=post_id))

    return render_template("edit_post.html", user=user, post=post)


@app.route("/post/new", methods=["GET", "POST"])
def new_post():
    user = session.get("user")
    if not user:
        return redirect(url_for("index"))
    
    # Requirement: only wallet users can post
    if user.get("login_type") != "wallet":
        return redirect(url_for("index"))
    
    if not user.get("is_staked"):
        return redirect(url_for("stake"))

            
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        
        if not title or not content:
            return "Missing title or content", 400
            
        if supabase:
            try:
                supabase.table("staking_posts").insert({
                    "title": title,
                    "content": content,
                    "user": user.get("id"),
                    "author_id": user.get("id"),
                    "author_name": user.get("name"),
                    "live": True
                }).execute()
                return redirect(url_for("index"))
            except Exception as e:
                logger.error(f"Error creating post: {str(e)}")
                return f"Error: {str(e)}", 500
        
        return redirect(url_for("index"))

    return render_template("create_post.html", user=user)


@app.route("/")
def index():
    user = session.get("user")
    
    if user:
        if "login_type" not in user or "id" not in user:
            session.pop("user", None)
            user = None
        elif user.get("login_type") == "wallet" and "address" not in user:
            session.pop("user", None)
            user = None

        
    # Pagination logic
    page = request.args.get("page", 1, type=int)
    page_size = 10
    offset = (page - 1) * page_size
    
    posts = []
    if supabase:
        try:
            # Execute the specified query with limit and calculated offset
            response = supabase.table("staking_posts") \
                .select("*") \
                .eq("live", True) \
                .eq("delete", False) \
                .order("id", desc=True) \
                .limit(page_size) \
                .offset(offset) \
                .execute()
            posts = response.data
        except Exception as e:
            logger.error(f"Error fetching posts: {str(e)}")
            
    return render_template("index.html", user=user, posts=posts, page=page)

@app.route("/post/<int:post_id>")
def post_detail(post_id):
    user = session.get("user")
    post = None
    if supabase:
        try:
            response = supabase.table("staking_posts") \
                .select("*") \
                .eq("delete", False) \
                .eq("id", post_id) \
                .single() \
                .execute()
            post = response.data
            
            # Render markdown
            if post and post.get("content"):
                post["html_content"] = markdown.markdown(post["content"], extensions=['fenced_code', 'tables'])
        except Exception as e:
            logger.error(f"Error fetching post {post_id}: {str(e)}")
            
    if not post:
        return "DECRYPT_ERROR: DATA_PACKET_NOT_FOUND", 404
        
    return render_template("post_detail.html", user=user, post=post)

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
            "username": user.user_metadata.get("user_name"), # X handle
            "login_type": "twitter"
        }
        
        logger.info(f"User {session['user']['name']} successfully logged in.")
        return redirect(url_for("index"))
    except Exception as e:
        logger.error(f"Failed to exchange code for session: {str(e)}")
        return f"Authentication failed during session exchange: {str(e)}", 401

@app.route("/dashboard")
def dashboard():
    user = session.get("user")
    if not user or "id" not in user or "login_type" not in user:
        session.pop("user", None)
        return redirect(url_for("index"))
    
    user_posts = []
    if supabase:
        try:
            response = supabase.table("staking_posts") \
                .select("*") \
                .eq("user", user["id"]) \
                .eq("delete", False) \
                .order("id", desc=True) \
                .execute()
            user_posts = response.data
        except Exception as e:
            logger.error(f"Error fetching user posts: {str(e)}")

    return render_template("dashboard.html", user=user, posts=user_posts)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, port=3000)
