import os
import re
import glob
import tempfile
import shutil

OUTPUT_FILE = "HASHES_made.txt"
BASE_DIR = "EFTA"


def parse_txt_file(filepath):
    last_modified = None
    etag = None
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Last-Modified:"):
                    last_modified = line.split("Last-Modified:", 1)[1].strip()
                elif line.startswith("Etag:"):
                    etag = line.split("Etag:", 1)[1].strip()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
    return last_modified, etag


def get_output_name(txt_filename):
    base = os.path.basename(txt_filename)
    if base.endswith("_pdf.txt"):
        return base[:-len("_pdf.txt")] + ".pdf"
    if base.endswith(".txt"):
        base = base[:-4]
    if not re.search(r'\.(pdf|mp4|mp3|jpg|jpeg|png|gif|mov|avi|mkv|doc|docx|xls|xlsx|zip|rar)$', base, re.IGNORECASE):
        base = base + ".pdf"
    return base


def get_number(name):
    m = re.search(r'EFTA(\d+)', os.path.basename(str(name)))
    return int(m.group(1)) if m else None


def collect_all_txt_files(base_dir):
    """Walk ALL subdirectories and collect every .txt file, sorted by EFTA number then filename."""
    all_files = []
    for root, dirs, files in os.walk(base_dir):
        dirs.sort()  # walk subdirs in alphabetical order
        for fname in files:
            if fname.endswith(".txt"):
                all_files.append(os.path.join(root, fname))

    # Sort by numeric EFTA number first, then by filename (to keep mp4/pdf etc. stable)
    all_files.sort(key=lambda f: (get_number(f) or 0, os.path.basename(f)))
    return all_files


def load_hashes_file(output_file):
    """Load HASHES_made.txt. Returns list of [name, line] and set of names."""
    entries = []
    existing = set()
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            for raw in f:
                stripped = raw.strip()
                if stripped:
                    name = stripped.split(":", 1)[0].strip()
                    entries.append([name, stripped])
                    existing.add(name)
    return entries, existing


def write_file(output_file, entries):
    """Atomically rewrite the output file."""
    dir_name = os.path.dirname(os.path.abspath(output_file)) or "."
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            for name, line in entries:
                f.write(line + "\n")
        shutil.move(tmp_path, output_file)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def main():
    if not os.path.isdir(BASE_DIR):
        print(f"ERROR: Directory '{BASE_DIR}' not found. Run this script from the parent folder of EFTA/.")
        return

    # Step 1: Collect ALL txt files across ALL subdirs, sorted by number
    print("Scanning all subdirectories in EFTA/ ...")
    all_files = collect_all_txt_files(BASE_DIR)
    print(f"Found {len(all_files)} .txt files total.")

    if all_files:
        first_num = get_number(all_files[0])
        last_num  = get_number(all_files[-1])
        print(f"Range: EFTA{first_num:08d} → EFTA{last_num:08d}")

    # Step 2: Load what's already in HASHES_made.txt
    print("\nLoading existing HASHES_made.txt...")
    entries, existing = load_hashes_file(OUTPUT_FILE)
    print(f"Already recorded: {len(existing)} entries.")

    # Step 3: Check for gaps in existing file
    recorded_numbers = {}  # number -> [names] (could have mp4 + pdf for same number)
    for name, line in entries:
        n = get_number(name)
        if n is not None:
            recorded_numbers.setdefault(n, []).append(name)

    all_file_numbers = set(get_number(f) for f in all_files if get_number(f) is not None)
    recorded_set = set(recorded_numbers.keys())
    gaps = sorted(all_file_numbers - recorded_set)

    if gaps:
        print(f"Found {len(gaps)} gap(s) — these numbers exist in EFTA/ but are missing from HASHES_made.txt.")
    else:
        print("No gaps found.")

    # Step 4: Build a combined ordered list
    # Strategy: merge everything — existing entries + new/gap entries — sorted by number then name
    # Use a dict keyed by output_name to deduplicate
    combined = {}  # output_name -> line

    # Seed with existing
    for name, line in entries:
        combined[name] = line

    # Process all files in order, adding anything not yet recorded
    new_count = 0
    skip_count = 0

    for filepath in all_files:
        output_name = get_output_name(filepath)
        if output_name in combined:
            skip_count += 1
            continue

        last_modified, etag = parse_txt_file(filepath)
        if last_modified and etag:
            combined[output_name] = f"{output_name}: {last_modified}, {etag}"
            new_count += 1
            if new_count % 10000 == 0:
                print(f"  Processed {new_count} new entries...")
        else:
            print(f"  Skipping (no data): {filepath}")

    print(f"\nNew entries to add: {new_count}  |  Skipped (exist): {skip_count}")

    # Step 5: Sort everything by EFTA number then output name
    print("Sorting all entries...")
    sorted_entries = sorted(
        combined.items(),
        key=lambda kv: (get_number(kv[0]) or 0, kv[0])
    )

    # Step 6: Write the fully sorted file
    print(f"Writing {len(sorted_entries)} entries to {OUTPUT_FILE} ...")
    write_file(OUTPUT_FILE, sorted_entries)

    print(f"\n=== Done ===")
    print(f"Total entries in file: {len(sorted_entries)}")
    print(f"New entries added:     {new_count}")
    print(f"Skipped (existed):     {skip_count}")


if __name__ == "__main__":
    main()
