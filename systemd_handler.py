import subprocess
import json
from typing import List, Dict, Optional
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/systemd", tags=["systemd"])


def run_systemctl(args: List[str]) -> str:
    """Run systemctl command and return output."""
    try:
        result = subprocess.run(
            ["systemctl"] + args,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="systemctl command timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_services():
    """List all systemd services with their status."""
    output = run_systemctl(["list-units", "--type=service", "--all", "--no-pager", "--output=json"])
    
    try:
        services = json.loads(output)
        return {"services": services}
    except json.JSONDecodeError:
        # Fallback to plain text parsing
        lines = output.strip().split("\n")
        services = []
        for line in lines:
            if line.strip() and not line.startswith("●") and not line.startswith("UNIT"):
                parts = line.split()
                if len(parts) >= 4:
                    services.append({
                        "unit": parts[0],
                        "load": parts[1],
                        "active": parts[2],
                        "sub": parts[3],
                        "description": " ".join(parts[4:]) if len(parts) > 4 else ""
                    })
        return {"services": services}


@router.get("/status/{service_name:path}")
async def get_service_status(service_name: str):
    """Get detailed status of a specific service."""
    output = run_systemctl(["status", service_name, "--no-pager", "--full"])
    
    # Also get service properties
    props_output = run_systemctl(["show", service_name, "--no-pager"])
    
    properties = {}
    for line in props_output.strip().split("\n"):
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    
    return {
        "service": service_name,
        "status_text": output,
        "properties": properties
    }


@router.post("/start/{service_name:path}")
async def start_service(service_name: str):
    """Start a systemd service."""
    try:
        subprocess.run(
            ["systemctl", "start", service_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=True
        )
        return {"ok": True, "service": service_name, "action": "started"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Failed to start service: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop/{service_name:path}")
async def stop_service(service_name: str):
    """Stop a systemd service."""
    try:
        subprocess.run(
            ["systemctl", "stop", service_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=True
        )
        return {"ok": True, "service": service_name, "action": "stopped"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Failed to stop service: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restart/{service_name:path}")
async def restart_service(service_name: str):
    """Restart a systemd service."""
    try:
        subprocess.run(
            ["systemctl", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=True
        )
        return {"ok": True, "service": service_name, "action": "restarted"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Failed to restart service: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enable/{service_name:path}")
async def enable_service(service_name: str):
    """Enable a systemd service."""
    try:
        subprocess.run(
            ["systemctl", "enable", service_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=True
        )
        return {"ok": True, "service": service_name, "action": "enabled"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Failed to enable service: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disable/{service_name:path}")
async def disable_service(service_name: str):
    """Disable a systemd service."""
    try:
        subprocess.run(
            ["systemctl", "disable", service_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=True
        )
        return {"ok": True, "service": service_name, "action": "disabled"}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=400, detail=f"Failed to disable service: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/{service_name:path}")
async def get_service_logs(service_name: str, lines: int = 100):
    """Get recent logs for a service."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return {"service": service_name, "logs": result.stdout}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

