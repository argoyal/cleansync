from pydantic import BaseModel


class SystemOut(BaseModel):
    app_name: str
    app_version: str
    python_version: str
    rclone_version: str
    storage_root: str
    data_dir: str
    watcher: str
    db_ok: bool
    insecure_http: bool
    public_url: str
