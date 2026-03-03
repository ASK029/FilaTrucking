from pathlib import Path

def get_file_structure_pathlib(start_path='./FilaTrucking'):
    """
    Prints a flat, recursive list of files and directories using pathlib.
    
    Args:
        start_path (str): The root directory to start the traversal.
    """
    p = Path(start_path)
    # Recursively find all files and directories
    for item in p.rglob('*'):
        if item.is_dir():
            print(f'Directory: {item.relative_to(p)}')
        else:
            print(f'File:      {item.relative_to(p)}')

# Example usage:
get_file_structure_pathlib()

