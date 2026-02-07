import chainlit as cl
from supabase import create_client, Client
import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Please set SUPABASE_URL and SUPABASE_ANON_KEY in .env file")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


class AuthManager:
    """Manages user authentication with Supabase"""
    
    @staticmethod
    async def sign_up(email: str, password: str, metadata: dict = None) -> dict:
        """Sign up a new user with optional metadata"""
        try:
            signup_data = {
                "email": email,
                "password": password
            }
            
            if metadata:
                signup_data["options"] = {"data": metadata}
            
            response = supabase.auth.sign_up(signup_data)
            
            return {
                "success": True,
                "user": response.user,
                "message": "Sign up successful! Please check your email to verify your account."
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Sign up failed: {str(e)}"
            }
    
    @staticmethod
    async def login(email: str, password: str) -> dict:
        """Log in an existing user"""
        try:
            response = supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })
            return {
                "success": True,
                "user": response.user,
                "session": response.session,
                "message": "Login successful!"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Login failed: {str(e)}"
            }
    
    @staticmethod
    async def logout() -> dict:
        """Log out the current user"""
        try:
            supabase.auth.sign_out()
            return {
                "success": True,
                "message": "Logged out successfully!"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Logout failed: {str(e)}"
            }
    
    @staticmethod
    def get_current_user() -> Optional[dict]:
        """Get the currently authenticated user"""
        try:
            user = supabase.auth.get_user()
            return user.user if user else None
        except:
            return None
    
    @staticmethod
    async def reset_password(email: str) -> dict:
        """Send password reset email"""
        try:
            supabase.auth.reset_password_email(email)
            return {
                "success": True,
                "message": "Password reset email sent! Check your inbox."
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Password reset failed: {str(e)}"
            }


@cl.password_auth_callback
async def auth_callback(username: str, password: str) -> Optional[cl.User]:
    """
    Chainlit's built-in password authentication callback
    This is called when a user tries to log in
    """
    # Attempt login with Supabase
    result = await AuthManager.login(username, password)
    
    if result["success"]:
        # Create Chainlit user object
        user = result["user"]
        return cl.User(
            identifier=user.email,
            metadata={
                "user_id": user.id,
                "email": user.email,
                "role": "user",
                "user_metadata": user.user_metadata
            }
        )
    else:
        # Return None to indicate failed authentication
        return None


@cl.on_chat_start
async def start():
    """Called when a new chat session starts"""
    user = cl.user_session.get("user")
    
    # Welcome message
    welcome_msg = f"""
👋 **Welcome to the AI Chatbot!**

You are logged in as: **{user.identifier}**

**Available Commands:**
- `/profile` - View your profile
- `/help` - Show help message
- `/logout` - Log out

How can I assist you today?
"""
    
    await cl.Message(content=welcome_msg).send()
    
    # Store user info in session
    cl.user_session.set("user_email", user.identifier)
    cl.user_session.set("user_metadata", user.metadata)


@cl.on_message
async def main(message: cl.Message):
    """Handle incoming messages"""
    user_email = cl.user_session.get("user_email")
    
    # Command handling
    command = message.content.strip().lower()
    
    if command == "/profile":
        await show_profile()
        return
    
    if command == "/help":
        await show_help()
        return
    
    if command == "/logout":
        await handle_logout()
        return
    
    # Regular chatbot response
    # Replace this with your actual chatbot logic
    response = f"""
You said: "{message.content}"

This is where your chatbot logic would process the message.
You can integrate your AI model, RAG system, or any other logic here.

Logged in as: {user_email}
"""
    
    await cl.Message(content=response).send()


async def show_profile():
    """Show user profile information"""
    user_metadata = cl.user_session.get("user_metadata")
    user_email = cl.user_session.get("user_email")
    
    profile_info = f"""
📋 **Your Profile**

**Email:** {user_email}
**User ID:** {user_metadata.get('user_id')}
**Role:** {user_metadata.get('role')}

**Additional Info:**
{user_metadata.get('user_metadata', {})}
"""
    
    await cl.Message(content=profile_info).send()


async def show_help():
    """Show help message"""
    help_msg = """
📚 **Help & Commands**

**Available Commands:**
- `/profile` - View your profile information
- `/help` - Show this help message
- `/logout` - Log out from your account

**How to use:**
Simply type your message and I'll respond!

**Need assistance?**
Contact support at support@example.com
"""
    
    await cl.Message(content=help_msg).send()


async def handle_logout():
    """Handle logout command"""
    result = await AuthManager.logout()
    await cl.Message(content=result["message"]).send()
    
    # Note: User will need to refresh/restart the app to log back in
    await cl.Message(
        content="Please refresh the page to log in again."
    ).send()


# Standalone sign-up script (run separately from main app)
async def standalone_signup():
    """
    Standalone function to create new users
    Run this separately: python -c "import asyncio; from chatbot_auth_enhanced import standalone_signup; asyncio.run(standalone_signup())"
    """
    print("=== User Sign Up ===")
    email = input("Enter email: ")
    password = input("Enter password: ")
    first_name = input("Enter first name (optional): ")
    last_name = input("Enter last name (optional): ")
    
    metadata = {}
    if first_name:
        metadata["first_name"] = first_name
    if last_name:
        metadata["last_name"] = last_name
    
    result = await AuthManager.sign_up(email, password, metadata)
    print(f"\n{result['message']}")


if __name__ == "__main__":
    # Run with: chainlit run chatbot_auth_enhanced.py -w
    pass
