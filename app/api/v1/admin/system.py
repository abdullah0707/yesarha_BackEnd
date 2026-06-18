from fastapi import APIRouter, Depends
import psutil
import requests

from app.core.deps import get_current_admin
from app.core.responses import success
from app.core.config import settings

router = APIRouter(prefix="/admin/system", tags=["Admin - System"])


@router.get("/resources")
def get_resources(_admin=Depends(get_current_admin)):

    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    gpu_data = []
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 4:
                    gpu_data.append({
                        "name": parts[0],
                        "usage_percent": float(parts[1]),
                        "memory_used_gb": round(float(parts[2]) / 1024, 2),
                        "memory_total_gb": round(float(parts[3]) / 1024, 2)
                    })
    except Exception:
        pass

    return success({
        "cpu_usage_percent": cpu,
        "ram_usage_percent": ram.percent,
        "ram_total_gb": round(ram.total / (1024 ** 3), 2),
        "ram_used_gb": round(ram.used / (1024 ** 3), 2),
        "gpu": gpu_data,
        "storage_used_gb": round(disk.used / (1024 ** 3), 2),
        "storage_total_gb": round(disk.total / (1024 ** 3), 2),
        "storage_percent": disk.percent
    })


@router.get("/health")
def get_system_health(_admin=Depends(get_current_admin)):

    ollama_status = "online"
    try:
        resp = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=2)
        if resp.status_code != 200:
            ollama_status = "offline"
    except Exception:
        ollama_status = "offline"

    from app.db.session import engine
    db_status = "online"
    try:
        with engine.connect():
            pass
    except Exception:
        db_status = "offline"

    chroma_status = "not_configured"

    return success({
        "api": "online",
        "database": db_status,
        "ollama": ollama_status,
        "chroma": chroma_status
    })
