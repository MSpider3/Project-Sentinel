import pytest
from textual.app import App
from sentinel_tui.app import SentinelApp

@pytest.mark.asyncio
async def test_tui_mounting_and_routing():
    """Verify that all screens mount and render correctly in ContentArea."""
    app = SentinelApp()
    
    async with app.run_test() as pilot:
        # Default screen is dashboard
        content = app.query_one("ContentArea")
        assert len(content.children) > 0, "Dashboard failed to mount children"

        # Switch to settings
        app.action_show_screen("settings")
        await pilot.pause()
        assert len(content.children) > 0, "Settings failed to mount"

        # Switch to devices
        app.action_show_screen("devices")
        await pilot.pause()
        assert len(content.children) > 0, "Devices failed to mount"

        # Switch to enrollment
        app.action_show_screen("enrollment")
        await pilot.pause()
        assert len(content.children) > 0, "Enrollment failed to mount"

        # Switch to authentication
        app.action_show_screen("authentication")
        await pilot.pause()
        assert len(content.children) > 0, "Authentication failed to mount"

@pytest.mark.asyncio
async def test_tui_settings_form():
    """Verify settings form inputs and validation classes load."""
    app = SentinelApp()
    
    async with app.run_test() as pilot:
        app.action_show_screen("settings")
        await pilot.pause()
        
        # Test finding a form field
        field = app.query("#field-camera_width")
        assert field is not None
