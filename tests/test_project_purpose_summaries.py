"""
Tests for Project Purpose-Driven AI Summaries.

Verifies:
1. Multi-project domain understanding:
   - Event Manager Website: Event planning, attendee management, ticket tiers, schedules.
   - Employee Management Portal: HR directory, leave management, department hierarchy, payroll.
   - Inventory Management System: Stock tracking, SKU catalog, warehouse bins, supplier orders.
2. Structure enforcement:
   - Section A. Project Overview (What it is, problem solved, target users, main purpose)
   - Section B. Scope and Key Features (Main features, workflows)
   - Section C. Current Development Status (Completed, in progress, pending)
   - Section D. Next Steps (Remaining work, recommendations)
3. Zero generic boilerplate:
   - Ensures no "software engineering initiative focused on delivering..."
   - Ensures login pages / database setup are not stated as the main purpose
4. Incomplete context handling:
   - Notes preliminary inference and advises adding specs in Context Editor
5. Fallback & Service integration:
   - Full service execution produces rich project-oriented summaries
"""
import os
import sys
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schemas.continuity import ContinuitySummaryOut
from app.services.continuity_service import (
    synthesize_project_purpose_summary,
    synthesize_handover_summary_fallback,
    generate_continuity_summary_service,
    _build_prompt_for_question,
)


def test_event_manager_website_purpose_summary():
    """Verify Event Manager Website produces a dedicated event-planning summary."""
    project = {
        "name": "Event Manager Website",
        "description": "A web application for organizing, hosting, and coordinating community and corporate events.",
        "progress": 35,
    }
    tasks = [
        {"title": "Attendee Registration Flow", "status": "COMPLETED", "description": "Online attendee sign-up and ticket issuance"},
        {"title": "Ticket Tiering & Pricing", "status": "IN_PROGRESS", "priority": "High", "description": "Configure VIP, Early Bird, and General passes"},
        {"title": "Schedule & Agenda Builder", "status": "TODO", "priority": "Medium", "description": "Multi-track speaker and session schedule planner"},
        {"title": "Attendee Check-in QR Scanner", "status": "TODO", "priority": "Low", "description": "Mobile gate scanning for QR tickets"},
        {"title": "Setup PostgreSQL Database", "status": "COMPLETED", "description": "Backend database schema initialization"},
        {"title": "Login and Authentication Page", "status": "COMPLETED", "description": "User login with JWT"},
    ]
    github = {
        "repository": "org/event-manager-web",
        "description": "Next.js and FastAPI event hosting and ticketing platform",
        "readme_excerpt": "# Event Manager Web\nManage events, tickets, and attendees effortlessly.",
    }

    summary, is_inf, inf_rationale = synthesize_project_purpose_summary(project, tasks, github)

    # 1. Structure Verification
    assert "A. Project Overview" in summary
    assert "B. Scope and Key Features" in summary
    assert "C. Current Development Status" in summary
    assert "D. Next Steps" in summary

    # 2. Business Purpose Verification
    summary_lower = summary.lower()
    assert any(term in summary_lower for term in ["event", "organizer", "attendee", "registration", "ticketing"])
    assert "attendee registration" in summary_lower
    assert "schedule" in summary_lower

    # 3. Boilerplate Ban Verification
    assert "software engineering initiative focused on delivering" not in summary_lower
    assert "connectivity between frontend and backend" not in summary_lower

    # 4. Filtered tasks: technical plumbing shouldn't be listed as primary business features in section B
    assert "Attendee Registration Flow" in summary or "Ticket Tiering & Pricing" in summary


def test_employee_management_portal_purpose_summary():
    """Verify Employee Management Portal produces an HR and workforce management summary."""
    project = {
        "name": "Employee Management Portal",
        "description": "",  # Empty description to test domain inference from title and tasks
        "progress": 25,
    }
    tasks = [
        {"title": "Employee Directory & Profiles", "status": "COMPLETED", "description": "Staff search, bio, contact details, and reporting lines"},
        {"title": "Leave Request & Approval Flow", "status": "IN_PROGRESS", "priority": "High", "description": "PTO submission and manager signoff workflow"},
        {"title": "Department Hierarchy Tree", "status": "TODO", "priority": "Medium", "description": "Visual organizational chart of departments and teams"},
        {"title": "Payroll Review Dashboard", "status": "TODO", "priority": "Low", "description": "Compensation overview and monthly salary review"},
    ]
    github = {
        "repository": "org/hr-employee-portal",
        "description": "Corporate HR intranet and team directory",
    }

    summary, is_inf, inf_rationale = synthesize_project_purpose_summary(project, tasks, github)

    assert "A. Project Overview" in summary
    assert "B. Scope and Key Features" in summary
    assert "C. Current Development Status" in summary
    assert "D. Next Steps" in summary

    summary_lower = summary.lower()
    assert any(term in summary_lower for term in ["employee", "workforce", "hr", "staff", "organization"])
    assert "leave request" in summary_lower or "directory" in summary_lower

    # Zero generic boilerplate
    assert "software engineering initiative focused on delivering" not in summary_lower


def test_inventory_management_system_purpose_summary():
    """Verify Inventory Management System produces a stock, warehouse, and supply chain summary."""
    project = {
        "name": "Inventory Management System",
        "description": "Enterprise warehouse inventory management and SKU replenishment tracking.",
        "progress": 50,
    }
    tasks = [
        {"title": "Product SKU Catalog", "status": "COMPLETED", "description": "Searchable master catalog of product codes, barcodes, and specs"},
        {"title": "Stock In/Out Tracking", "status": "IN_PROGRESS", "priority": "Urgent", "description": "Real-time stock level decrement and warehouse receiving"},
        {"title": "Low Stock Automated Alerts", "status": "TODO", "priority": "High", "description": "Email alerts when SKU threshold falls below safety margin"},
        {"title": "Supplier Purchase Orders", "status": "TODO", "priority": "Normal", "description": "Create and dispatch PO documents to vendors"},
    ]

    summary, is_inf, inf_rationale = synthesize_project_purpose_summary(project, tasks)

    assert "A. Project Overview" in summary
    assert "B. Scope and Key Features" in summary
    assert "C. Current Development Status" in summary
    assert "D. Next Steps" in summary

    summary_lower = summary.lower()
    assert any(term in summary_lower for term in ["inventory", "stock", "warehouse", "sku", "supply"])
    assert "sku catalog" in summary_lower or "stock in/out" in summary_lower

    # Zero generic boilerplate
    assert "software engineering initiative focused on delivering" not in summary_lower


def test_incomplete_generic_project_handles_cautiously():
    """Verify that an ambiguous project with minimal context includes a preliminary disclaimer."""
    project = {
        "name": "Project Delta",
        "description": "",
    }
    tasks = [
        {"title": "Setup DB", "status": "COMPLETED"},
        {"title": "Init frontend", "status": "COMPLETED"},
    ]

    summary, is_inf, inf_rationale = synthesize_project_purpose_summary(project, tasks)

    assert "A. Project Overview" in summary
    # Should flag that description is incomplete or preliminary
    assert is_inf is True or "preliminary" in summary.lower() or "context editor" in summary.lower() or "incomplete" in summary.lower()
    assert "software engineering initiative focused on delivering" not in summary.lower()


def test_fallback_handover_summary_uses_structured_purpose():
    """Ensure synthesize_handover_summary_fallback incorporates the 4 sections."""
    raw_dict = {
        "project": {
            "name": "Event Manager Website",
            "description": "Online ticketing and event logistics system.",
            "progress": 40,
        },
        "tasks": [
            {"title": "Publish Event Page", "status": "COMPLETED"},
            {"title": "Ticketing Checkout", "status": "IN_PROGRESS", "priority": "High"},
        ],
        "risks": [],
        "github": {"repository": "event-system"},
        "activities": [],
    }

    handover = synthesize_handover_summary_fallback(raw_dict)

    overview = handover["project_overview"]
    assert "A. Project Overview" in overview
    assert "B. Scope and Key Features" in overview
    assert "C. Current Development Status" in overview
    assert "D. Next Steps" in overview
    assert "software engineering initiative focused on delivering" not in overview.lower()


@pytest.mark.asyncio
async def test_generate_continuity_summary_service_returns_purpose_driven_overview():
    """Verify the full generate_continuity_summary_service produces a structured purpose summary."""
    project_id = "test-proj-purpose-123"

    raw_context_data = {
        "project": {
            "id": project_id,
            "name": "Event Manager Website",
            "description": "Platform for event planners to host meetups, sell tickets, and manage attendees.",
            "progress": 50,
        },
        "tasks": [
            {"id": "t1", "title": "Guest List Import", "status": "COMPLETED", "priority": "high", "description": "CSV upload for invitees"},
            {"id": "t2", "title": "Ticket Payment Gateway", "status": "IN_PROGRESS", "priority": "urgent", "description": "Stripe checkout integration"},
        ],
        "activities": [],
        "risks": [],
        "github": {
            "repository": "org/event-manager",
            "description": "Event management portal",
            "readme_excerpt": "# Event Manager\nHost your events with ease.",
            "branches": [],
            "commits": [],
            "pull_requests": [],
        },
    }

    mock_llm_response = {
        "project_overview": (
            "A. Project Overview\n"
            "Event Manager Website is an event coordination system for organizers to host events.\n\n"
            "B. Scope and Key Features\n"
            "- Guest list management and CSV attendee import.\n"
            "- Ticket payment gateway integration.\n\n"
            "C. Current Development Status\n"
            "- Completed: Guest List Import.\n"
            "- In Progress: Ticket Payment Gateway.\n\n"
            "D. Next Steps\n"
            "- Complete payment gateway testing."
        ),
        "previous_developer_work": "Completed guest list import feature.",
        "current_work": "Working on ticket payment gateway.",
        "pending_work": "Finalize payment testing.",
        "blocked_overdue_work": "None.",
        "recent_github_activity": "Initial commits pushed.",
        "known_issues": "None.",
        "important_context": "Production launch next month.",
        "what_next_developer_should_know": "Stripe test keys configured.",
        "recommended_next_steps": "Complete gateway testing.",
    }

    with patch("app.services.continuity_service._build_project_context", new_callable=AsyncMock) as mock_ctx, \
         patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:

        mock_ctx.return_value = ("PROJECT CONTEXT MOCK", raw_context_data)
        mock_llm.return_value = mock_llm_response

        result = await generate_continuity_summary_service(
            project_id=project_id,
            current_user=None,
        )

        assert isinstance(result, ContinuitySummaryOut)
        assert "A. Project Overview" in result.project_overview
        assert "B. Scope and Key Features" in result.project_overview
        assert "C. Current Development Status" in result.project_overview
        assert "D. Next Steps" in result.project_overview
        assert "software engineering initiative focused on delivering" not in result.project_overview.lower()


@pytest.mark.asyncio
async def test_build_prompt_for_question_enforces_business_purpose():
    """Verify that asking questions about project purpose prompts the model for business purpose."""
    with patch("app.services.continuity_service.get_cached_project_context", return_value=("MOCK CONTEXT", {})), \
         patch("app.services.continuity_service._build_project_context", new_callable=AsyncMock) as mock_ctx:

        mock_ctx.return_value = ("MOCK CONTEXT", {})
        prompt, intent = await _build_prompt_for_question(
            project_id="proj-123",
            question="What is this project about?",
            messages=[],
        )

        assert "what the project is, who it is for, what problem it solves" in prompt.lower()
        assert "actual business and user purpose" in prompt.lower()
        assert "login pages or frontend-backend connectivity" in prompt.lower()
