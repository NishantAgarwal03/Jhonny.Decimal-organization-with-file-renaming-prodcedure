import os
import csv
import argparse

def scan_drive(target_dir, output_csv):
    """
    Scans the target directory and creates a CSV containing only 'name' and 'type' (extension).
    """
    print(f"Scanning '{target_dir}'...")
    
    with open(output_csv, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['path', 'name', 'type'])
        
        count = 0
        def on_error(err):
            pass
            
        for root, dirs, files in os.walk(target_dir, onerror=on_error):
            for file in files:
                file_path = os.path.join(root, file)
                _, path_no_drive = os.path.splitdrive(file_path)
                path_no_drive = path_no_drive.lstrip('\\').lstrip('/')
                
                name, ext = os.path.splitext(file)
                writer.writerow([path_no_drive, name, ext.lower()])
                count += 1
                
    print(f"Finished! Found {count} files and saved to {output_csv}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('target_dir', type=str, help='Directory to scan')
    args = parser.parse_args()
    
    target_dir = args.target_dir
    # Safely extract folder or drive name for session isolation
    base_name = os.path.basename(os.path.normpath(target_dir))
    if not base_name or base_name == '.':
        base_name = target_dir.replace(":\\", "_Drive").replace(":", "_Drive").replace("\\", "").replace("/", "")
        if not base_name:
            base_name = "root"
            
    session_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "runs", f"session_{base_name}")
    os.makedirs(session_dir, exist_ok=True)
    out_csv = os.path.join(session_dir, 'drive_scan.csv')
    
    scan_drive(target_dir, out_csv)
