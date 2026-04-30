import os

def bridge_path(env_var: str, default_filename: str) -> str:
    path = os.environ.get(env_var)
    if path:
        return path
    
    # Check current directory
    local_path = os.path.join(os.getcwd(), 'achelion_arms', 'state', default_filename)
    if os.path.exists(local_path):
        return local_path
        
    # Default to relative path from src/engine
    return os.path.join('achelion_arms', 'state', default_filename)
