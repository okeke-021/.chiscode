"""
User Management Utility Script
Run this script to manage users (sign up, list users, etc.)
"""

import asyncio
from supabase import create_client, Client
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # For admin operations

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Please set SUPABASE_URL and SUPABASE_ANON_KEY in .env file")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def create_user(email: str, password: str, first_name: str = "", last_name: str = ""):
    """Create a new user account"""
    try:
        metadata = {}
        if first_name:
            metadata["first_name"] = first_name
        if last_name:
            metadata["last_name"] = last_name
        
        signup_data = {
            "email": email,
            "password": password
        }
        
        if metadata:
            signup_data["options"] = {"data": metadata}
        
        response = supabase.auth.sign_up(signup_data)
        
        print(f"✅ User created successfully!")
        print(f"Email: {response.user.email}")
        print(f"User ID: {response.user.id}")
        print(f"⚠️  Please check email for verification link")
        
        return response.user
        
    except Exception as e:
        print(f"❌ Error creating user: {str(e)}")
        return None


async def interactive_signup():
    """Interactive sign-up process"""
    print("\n" + "="*50)
    print("USER REGISTRATION")
    print("="*50)
    
    email = input("Email: ").strip()
    password = input("Password (min 6 characters): ").strip()
    
    if len(password) < 6:
        print("❌ Password must be at least 6 characters")
        return
    
    confirm_password = input("Confirm Password: ").strip()
    
    if password != confirm_password:
        print("❌ Passwords do not match")
        return
    
    first_name = input("First Name (optional): ").strip()
    last_name = input("Last Name (optional): ").strip()
    
    print("\nCreating user...")
    await create_user(email, password, first_name, last_name)


async def test_login(email: str, password: str):
    """Test login credentials"""
    try:
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        print(f"✅ Login successful!")
        print(f"Email: {response.user.email}")
        print(f"User ID: {response.user.id}")
        
        return response.user
        
    except Exception as e:
        print(f"❌ Login failed: {str(e)}")
        return None


async def interactive_login_test():
    """Interactive login test"""
    print("\n" + "="*50)
    print("TEST LOGIN")
    print("="*50)
    
    email = input("Email: ").strip()
    password = input("Password: ").strip()
    
    print("\nAttempting login...")
    await test_login(email, password)


def main_menu():
    """Display main menu"""
    print("\n" + "="*50)
    print("SUPABASE USER MANAGEMENT UTILITY")
    print("="*50)
    print("1. Create new user (sign up)")
    print("2. Test login")
    print("3. Exit")
    print("="*50)
    
    choice = input("\nSelect an option (1-3): ").strip()
    return choice


async def main():
    """Main function"""
    while True:
        choice = main_menu()
        
        if choice == "1":
            await interactive_signup()
        elif choice == "2":
            await interactive_login_test()
        elif choice == "3":
            print("\nGoodbye!")
            break
        else:
            print("❌ Invalid option. Please select 1, 2, or 3.")
        
        input("\nPress Enter to continue...")


if __name__ == "__main__":
    asyncio.run(main())
