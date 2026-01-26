"""
ChisCode: AI Agent Builder - Main Chainlit Application
Entry point for the AI-powered code generation platform
"""

import os
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime

import chainlit as cl
from chainlit.input_widget import Select, TextInput, Slider
from langchain.memory import ConversationBufferMemory
from langchain.schema import HumanMessage, AIMessage

# Internal imports
from app.config import settings
from app.auth.chainlit_auth import authenticate_user, get_user_session
from app.auth.rate_limiter import check_rate_limit, RateLimitExceeded
from app.agents.orchestrator import AgentOrchestrator
from app.agents.code_generator import CodeGeneratorAgent
from app.agents.tech_stack_selector import TechStackSelector
from app.integrations.github_client import GitHubClient
from app.integrations.pinecone_client import PineconeClient
from app.integrations.polar_payments import PolarPayments
from app.models.schemas import (
    ProjectCreate,
    UserSession,
    SubscriptionTier,
    GenerationStatus
)
from app.utils.logger import get_logger
from app.utils.helpers import format_code_files, create_artifact

# Initialize logger
logger = get_logger(__name__)

# Initialize services
orchestrator: Optional[AgentOrchestrator] = None
pinecone_client: Optional[PineconeClient] = None
polar_client: Optional[PolarPayments] = None


# ============================================================================
# CHAINLIT LIFECYCLE HOOKS
# ============================================================================

@cl.oauth_callback
async def oauth_callback(
    provider_id: str,
    token: str,
    raw_user_data: Dict[str, Any],
    default_user: cl.User,
) -> Optional[cl.User]:
    """
    OAuth callback for Google authentication
    """
    try:
        # Authenticate user and get subscription info
        user = await authenticate_user(
            provider_id=provider_id,
            token=token,
            user_data=raw_user_data
        )
        
        if user:
            logger.info(f"User authenticated: {user.identifier} ({user.metadata.get('tier', 'free')})")
            return user
        
        logger.warning(f"Authentication failed for provider: {provider_id}")
        return None
        
    except Exception as e:
        logger.error(f"OAuth callback error: {str(e)}")
        return None


@cl.on_chat_start
async def on_chat_start():
    """
    Initialize chat session when user starts conversation
    """
    try:
        # Get authenticated user
        user = cl.user_session.get("user")
        
        if not user:
            await cl.Message(
                content="⚠️ Please sign in to use ChisCode. Click the login button above.",
                author="System"
            ).send()
            return
        
        # Initialize session data
        user_session = await get_user_session(user.identifier)
        cl.user_session.set("user_data", user_session)
        
        # Initialize memory for conversation
        memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True
        )
        cl.user_session.set("memory", memory)
        
        # Initialize orchestrator
        global orchestrator
        if not orchestrator:
            orchestrator = AgentOrchestrator(settings)
        cl.user_session.set("orchestrator", orchestrator)
        
        # Initialize project state
        cl.user_session.set("current_project", None)
        cl.user_session.set("generation_status", GenerationStatus.IDLE)
        
        # Get subscription info
        tier = user_session.get("subscription_tier", SubscriptionTier.FREE)
        requests_remaining = user_session.get("requests_remaining", 5)
        
        # Welcome message with user info
        welcome_content = f"""# Welcome to ChisCode! 🚀

**Subscription:** {tier.value.upper()} tier
**Requests Remaining Today:** {requests_remaining}

I'm your AI-powered code generation assistant. I can transform your ideas into production-ready web applications!

## Quick Start

Choose one of these options to get started:
"""
        
        await cl.Message(content=welcome_content, author="ChisCode").send()
        
        # Create starter buttons
        actions = [
            cl.Action(
                name="new_app",
                value="new_app",
                label="🆕 Create New App",
                description="Describe your app and I'll build it"
            ),
            cl.Action(
                name="view_templates",
                value="view_templates",
                label="📚 View Templates",
                description="Browse available frameworks and templates"
            ),
            cl.Action(
                name="resume_project",
                value="resume_project",
                label="🔄 Resume Project",
                description="Continue working on a previous project"
            ),
            cl.Action(
                name="upgrade",
                value="upgrade",
                label="⭐ Upgrade Plan",
                description="Get more requests and features"
            )
        ]
        
        await cl.Message(
            content="What would you like to do?",
            actions=actions,
            author="ChisCode"
        ).send()
        
        logger.info(f"Chat started for user: {user.identifier}")
        
    except Exception as e:
        logger.error(f"Error in on_chat_start: {str(e)}")
        await cl.Message(
            content=f"❌ Error initializing session: {str(e)}",
            author="System"
        ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """
    Handle incoming messages from user
    """
    try:
        # Get user session
        user = cl.user_session.get("user")
        user_data = cl.user_session.get("user_data")
        
        if not user or not user_data:
            await cl.Message(
                content="⚠️ Session expired. Please refresh and sign in again.",
                author="System"
            ).send()
            return
        
        # Check rate limit
        try:
            await check_rate_limit(user.identifier, user_data.get("subscription_tier"))
        except RateLimitExceeded as e:
            await cl.Message(
                content=f"⚠️ {str(e)}\n\nUpgrade your plan for more requests: /upgrade",
                author="System"
            ).send()
            return
        
        # Get memory and orchestrator
        memory = cl.user_session.get("memory")
        orchestrator = cl.user_session.get("orchestrator")
        
        # Add user message to memory
        memory.chat_memory.add_message(HumanMessage(content=message.content))
        
        # Check if this is a command
        if message.content.startswith("/"):
            await handle_command(message.content)
            return
        
        # Check current generation status
        status = cl.user_session.get("generation_status")
        
        if status == GenerationStatus.GENERATING:
            await cl.Message(
                content="⏳ Please wait... I'm still generating your previous request.",
                author="System"
            ).send()
            return
        
        # Process the message through orchestrator
        await process_user_request(message.content, orchestrator, memory)
        
    except Exception as e:
        logger.error(f"Error in on_message: {str(e)}")
        await cl.Message(
            content=f"❌ An error occurred: {str(e)}",
            author="System"
        ).send()


@cl.on_stop
async def on_stop():
    """
    Handle user stopping the current task
    """
    try:
        user = cl.user_session.get("user")
        cl.user_session.set("generation_status", GenerationStatus.CANCELLED)
        
        await cl.Message(
            content="⏸️ Task cancelled. You can start a new request anytime!",
            author="System"
        ).send()
        
        logger.info(f"Task stopped by user: {user.identifier if user else 'unknown'}")
        
    except Exception as e:
        logger.error(f"Error in on_stop: {str(e)}")


@cl.on_chat_end
async def on_chat_end():
    """
    Handle chat session end
    """
    try:
        user = cl.user_session.get("user")
        
        # Save any unsaved work
        current_project = cl.user_session.get("current_project")
        if current_project:
            # TODO: Implement project saving logic
            pass
        
        logger.info(f"Chat ended for user: {user.identifier if user else 'unknown'}")
        
    except Exception as e:
        logger.error(f"Error in on_chat_end: {str(e)}")


@cl.on_chat_resume
async def on_chat_resume(thread: Dict):
    """
    Handle user resuming a previous chat session
    """
    try:
        user = cl.user_session.get("user")
        
        if not user:
            await cl.Message(
                content="⚠️ Please sign in to resume your session.",
                author="System"
            ).send()
            return
        
        # Restore session data
        user_session = await get_user_session(user.identifier)
        cl.user_session.set("user_data", user_session)
        
        # Restore memory from thread
        memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True
        )
        
        # Restore chat history
        if thread and "steps" in thread:
            for step in thread["steps"]:
                if step["type"] == "user_message":
                    memory.chat_memory.add_message(HumanMessage(content=step["output"]))
                elif step["type"] == "assistant_message":
                    memory.chat_memory.add_message(AIMessage(content=step["output"]))
        
        cl.user_session.set("memory", memory)
        
        # Restore orchestrator
        global orchestrator
        if not orchestrator:
            orchestrator = AgentOrchestrator(settings)
        cl.user_session.set("orchestrator", orchestrator)
        
        await cl.Message(
            content="👋 Welcome back! Your session has been restored. What would you like to work on?",
            author="ChisCode"
        ).send()
        
        logger.info(f"Chat resumed for user: {user.identifier}")
        
    except Exception as e:
        logger.error(f"Error in on_chat_resume: {str(e)}")
        await cl.Message(
            content=f"❌ Error resuming session: {str(e)}",
            author="System"
        ).send()


@cl.action_callback("new_app")
async def on_new_app_action(action: cl.Action):
    """
    Handle 'Create New App' action
    """
    await cl.Message(
        content="""## Let's Build Your App! 🏗️

Please describe the application you want to build. Be as detailed as possible:

**Good example:**
"Create a task management app with user authentication, the ability to create/edit/delete tasks, assign due dates, and mark tasks as complete. Use React for the frontend with Material-UI, FastAPI for the backend, and PostgreSQL for the database."

**What to include:**
- Main features and functionality
- User interactions
- Preferred technologies (optional)
- Any specific requirements

Go ahead and describe your app below! 👇""",
        author="ChisCode"
    ).send()
    
    # Set state to waiting for app description
    cl.user_session.set("awaiting_input", "app_description")


@cl.action_callback("view_templates")
async def on_view_templates_action(action: cl.Action):
    """
    Handle 'View Templates' action
    """
    templates_info = """## Available Frameworks & Templates 📚

### Frontend Frameworks
- **React** - Component-based UI library
- **Next.js** - React framework with SSR and routing
- **Vue.js** - Progressive JavaScript framework
- **Angular** - Full-featured TypeScript framework

### Backend Frameworks
- **FastAPI** - Modern Python API framework
- **Django** - Full-stack Python framework
- **Flask** - Lightweight Python framework
- **Express** - Node.js web framework
- **Chainlit** - Python framework for AI chat applications

### Styling Options
- Tailwind CSS
- Material-UI
- Bootstrap
- Styled Components

### Databases
- PostgreSQL
- MongoDB
- MySQL
- SQLite

I can work with any combination of these technologies! Just describe your app and I'll recommend the best stack, or you can specify your preferences.

Ready to start? Just describe your app!"""
    
    await cl.Message(content=templates_info, author="ChisCode").send()


@cl.action_callback("resume_project")
async def on_resume_project_action(action: cl.Action):
    """
    Handle 'Resume Project' action
    """
    # TODO: Implement project history retrieval
    await cl.Message(
        content="""## Resume Previous Project 🔄

Project history feature coming soon! For now, you can:
- Start a new project
- Ask me to recreate a previous project by describing it again

What would you like to do?""",
        author="ChisCode"
    ).send()


@cl.action_callback("upgrade")
async def on_upgrade_action(action: cl.Action):
    """
    Handle 'Upgrade Plan' action
    """
    user_data = cl.user_session.get("user_data")
    current_tier = user_data.get("subscription_tier", SubscriptionTier.FREE)
    
    pricing_info = f"""## Upgrade Your Plan ⭐

**Current Plan:** {current_tier.value.upper()}

### Available Plans

**🆓 Free Tier**
- 5 requests per day
- Basic features
- Community support

**💼 Basic Plan - $29/month**
- 100 requests per day
- All frameworks supported
- Priority support
- GitHub integration
- Live preview

**🚀 Pro Plan - $150/month**
- 1000 requests per day
- Advanced features
- Dedicated support
- Auto-deployment
- Custom templates
- Team collaboration

Visit our pricing page to upgrade: {settings.app_base_url}/pricing

Need help choosing? Just ask!"""
    
    await cl.Message(content=pricing_info, author="ChisCode").send()


# ============================================================================
# CORE PROCESSING FUNCTIONS
# ============================================================================

async def process_user_request(
    user_input: str,
    orchestrator: AgentOrchestrator,
    memory: ConversationBufferMemory
):
    """
    Process user request through the agent orchestrator
    """
    try:
        # Set status to generating
        cl.user_session.set("generation_status", GenerationStatus.GENERATING)
        
        # Create thinking message
        thinking_msg = cl.Message(
            content="🤔 Analyzing your request...",
            author="ChisCode"
        )
        await thinking_msg.send()
        
        # Step 1: Analyze requirements
        async with cl.Step(name="Analyzing Requirements", type="tool") as step:
            requirements = await orchestrator.analyze_requirements(user_input)
            step.output = f"Identified {len(requirements.get('features', []))} key features"
        
        # Step 2: Select tech stack
        async with cl.Step(name="Selecting Tech Stack", type="tool") as step:
            tech_stack = await orchestrator.select_tech_stack(requirements)
            
            stack_summary = f"""**Recommended Stack:**
- Frontend: {tech_stack['frontend']['framework']}
- Backend: {tech_stack['backend']['framework']}
- Database: {tech_stack['database']['type']}
- Styling: {tech_stack['styling']['library']}
"""
            step.output = stack_summary
            
            # Ask for confirmation
            await thinking_msg.remove()
            
            confirm_msg = await cl.AskActionMessage(
                content=f"## Recommended Technology Stack\n\n{stack_summary}\n\nProceed with this stack?",
                actions=[
                    cl.Action(name="confirm", value="yes", label="✅ Yes, proceed"),
                    cl.Action(name="modify", value="no", label="✏️ Modify stack"),
                ],
                author="ChisCode"
            ).send()
            
            if confirm_msg and confirm_msg.get("value") == "no":
                await cl.Message(
                    content="Please specify your preferred technologies:",
                    author="ChisCode"
                ).send()
                cl.user_session.set("awaiting_input", "custom_stack")
                cl.user_session.set("generation_status", GenerationStatus.IDLE)
                return
        
        # Step 3: Generate code
        async with cl.Step(name="Generating Code", type="llm") as step:
            generation_msg = cl.Message(
                content="",
                author="Codestral"
            )
            await generation_msg.send()
            
            # Stream code generation
            generated_files = {}
            async for update in orchestrator.generate_code_stream(
                requirements=requirements,
                tech_stack=tech_stack
            ):
                if update["type"] == "file":
                    generated_files[update["filename"]] = update["content"]
                    await generation_msg.stream_token(f"\n✅ Generated: `{update['filename']}`")
                elif update["type"] == "progress":
                    await generation_msg.stream_token(f"\n⏳ {update['message']}")
            
            await generation_msg.update()
            step.output = f"Generated {len(generated_files)} files"
        
        # Step 4: Validate code
        async with cl.Step(name="Validating Code", type="tool") as step:
            validation_result = await orchestrator.validate_code(generated_files)
            
            if not validation_result["valid"]:
                # Reflection: Fix errors
                async with cl.Step(name="Fixing Issues", type="llm") as fix_step:
                    generated_files = await orchestrator.fix_code_issues(
                        generated_files,
                        validation_result["errors"]
                    )
                    fix_step.output = "Issues resolved"
            
            step.output = "Code validation passed ✅"
        
        # Step 5: Create preview (if enabled)
        preview_url = None
        if settings.feature_live_preview:
            async with cl.Step(name="Creating Preview", type="tool") as step:
                preview_url = await orchestrator.create_preview(generated_files)
                step.output = f"Preview available at: {preview_url}"
        
        # Step 6: Prepare deployment options
        deployment_options = await prepare_deployment_options(generated_files)
        
        # Save project
        project = ProjectCreate(
            name=requirements.get("project_name", "My App"),
            description=user_input,
            tech_stack=tech_stack,
            files=generated_files,
            preview_url=preview_url
        )
        cl.user_session.set("current_project", project)
        
        # Send completion message
        completion_content = f"""## ✅ Your App is Ready!

**Project:** {project.name}
**Files Generated:** {len(generated_files)}
"""
        
        if preview_url:
            completion_content += f"\n**Live Preview:** {preview_url}"
        
        completion_content += "\n\n### Next Steps:\n\n"
        
        # Create action buttons for next steps
        actions = [
            cl.Action(
                name="view_code",
                value="view_code",
                label="👀 View Code",
                description="Review generated files"
            ),
            cl.Action(
                name="push_github",
                value="push_github",
                label="📤 Push to GitHub",
                description="Create repository and commit code"
            ),
            cl.Action(
                name="deploy",
                value="deploy",
                label="🚀 Deploy",
                description="Deploy to production"
            ),
            cl.Action(
                name="download",
                value="download",
                label="💾 Download ZIP",
                description="Download project locally"
            )
        ]
        
        await cl.Message(
            content=completion_content,
            actions=actions,
            author="ChisCode"
        ).send()
        
        # Add to memory
        memory.chat_memory.add_message(
            AIMessage(content=f"Successfully generated {project.name}")
        )
        
        # Reset status
        cl.user_session.set("generation_status", GenerationStatus.COMPLETED)
        
        logger.info(f"Code generation completed: {project.name}")
        
    except Exception as e:
        logger.error(f"Error in process_user_request: {str(e)}")
        cl.user_session.set("generation_status", GenerationStatus.FAILED)
        
        await cl.Message(
            content=f"""## ❌ Generation Failed

An error occurred while generating your app:
{str(e)}
Please try again or contact support if the issue persists.""",
            author="System"
        ).send()


async def handle_command(command: str):
    """
    Handle special commands
    """
    command = command.lower().strip()
    
    if command == "/help":
        help_text = """## Available Commands

- `/help` - Show this help message
- `/upgrade` - View upgrade options
- `/history` - View project history
- `/reset` - Reset current session
- `/status` - Check your account status
- `/deploy [platform]` - Deploy current project

For general questions, just type naturally!"""
        
        await cl.Message(content=help_text, author="System").send()
    
    elif command == "/upgrade":
        await on_upgrade_action(None)
    
    elif command == "/status":
        user_data = cl.user_session.get("user_data")
        tier = user_data.get("subscription_tier", SubscriptionTier.FREE)
        requests_remaining = user_data.get("requests_remaining", 0)
        
        status_text = f"""## Account Status

**Subscription Tier:** {tier.value.upper()}
**Requests Remaining Today:** {requests_remaining}
**Member Since:** {user_data.get('created_at', 'N/A')}

Need more requests? Use `/upgrade` to view plans."""
        
        await cl.Message(content=status_text, author="System").send()
    
    elif command == "/reset":
        # Reset session
        cl.user_session.set("current_project", None)
        cl.user_session.set("generation_status", GenerationStatus.IDLE)
        
        memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True
        )
        cl.user_session.set("memory", memory)
        
        await cl.Message(
            content="✅ Session reset. Ready for a new project!",
            author="System"
        ).send()
    
    else:
        await cl.Message(
            content=f"Unknown command: `{command}`. Type `/help` for available commands.",
            author="System"
        ).send()


async def prepare_deployment_options(files: Dict[str, str]) -> Dict[str, Any]:
    """
    Prepare deployment options based on project structure
    """
    # Analyze project to determine best deployment platforms
    has_nextjs = "next.config.js" in files or "next.config.ts" in files
    has_react = "package.json" in files and "react" in files.get("package.json", "")
    has_django = "manage.py" in files
    has_fastapi = any("fastapi" in content for content in files.values())
    
    options = {
        "recommended": [],
        "supported": []
    }
    
    if has_nextjs:
        options["recommended"].append("vercel")
        options["supported"].extend(["netlify", "railway"])
    elif has_react:
        options["recommended"].append("netlify")
        options["supported"].extend(["vercel", "render"])
    
    if has_django or has_fastapi:
        options["recommended"].append("railway")
        options["supported"].extend(["render", "fly.io", "aws"])
    
    return options


# ============================================================================
# ACTION CALLBACKS FOR GENERATED PROJECT
# ============================================================================

@cl.action_callback("view_code")
async def on_view_code(action: cl.Action):
    """View generated code files"""
    project = cl.user_session.get("current_project")
    
    if not project:
        await cl.Message(content="No project available", author="System").send()
        return
    
    # Create artifact with code
    files_content = format_code_files(project.files)
    
    await cl.Message(
        content=f"## Generated Code for {project.name}\n\n{files_content}",
        author="ChisCode"
    ).send()


@cl.action_callback("push_github")
async def on_push_github(action: cl.Action):
    """Push code to GitHub"""
    project = cl.user_session.get("current_project")
    user = cl.user_session.get("user")
    
    if not project:
        await cl.Message(content="No project available", author="System").send()
        return
    
    # Ask for repository name
    res = await cl.AskUserMessage(
        content="What should we name the GitHub repository?",
        author="ChisCode",
        timeout=60
    ).send()
    
    if res:
        repo_name = res["output"]
        
        async with cl.Step(name="Creating GitHub Repository", type="tool") as step:
            github_client = GitHubClient(settings)
            repo_url = await github_client.create_and_push(
                repo_name=repo_name,
                files=project.files,
                description=project.description
            )
            step.output = f"Repository created: {repo_url}"
        
        await cl.Message(
            content=f"✅ Code pushed to GitHub!\n\n**Repository:** {repo_url}",
            author="ChisCode"
        ).send()


@cl.action_callback("deploy")
async def on_deploy(action: cl.Action):
    """Deploy the application"""
    project = cl.user_session.get("current_project")
    
    if not project:
        await cl.Message(content="No project available", author="System").send()
        return
    
    # TODO: Implement deployment logic
    await cl.Message(
        content="Deployment feature coming soon! For now, you can download the code and deploy manually.",
        author="ChisCode"
    ).send()


@cl.action_callback("download")
async def on_download(action: cl.Action):
    """Download project as ZIP"""
    project = cl.user_session.get("current_project")
    
    if not project:
        await cl.Message(content="No project available", author="System").send()
        return
    
    # Create ZIP file
    import zipfile
    import io
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in project.files.items():
            zip_file.writestr(filename, content)
    
    zip_buffer.seek(0)
    
    # Send as file
    elements = [
        cl.File(
            name=f"{project.name}.zip",
            content=zip_buffer.getvalue(),
            display="inline"
        )
    ]
    
    await cl.Message(
        content=f"✅ Your project is ready to download!",
        elements=elements,
        author="ChisCode"
    ).send()


# ============================================================================
# APPLICATION STARTUP
# ============================================================================

if __name__ == "__main__":
    # This is handled by Chainlit CLI: chainlit run app/main.py
    pass
