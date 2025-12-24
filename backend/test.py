try:
    from backend.scheduler_runner import main
    print("IMPORT SUCCESS")
except Exception as e:
    print("IMPORT FAILED:", e)