#!/usr/bin/env python3
"""
Script to check HASHES.txt for duplicate lines and out-of-order entries,
then create a sorted and deduplicated version as HASHES2.txt

Strictly extracts ONLY the 8-digit number after EFTA (anywhere in the line)
and ignores everything else (path, extension, colon, headers, etc.).
Sorting is purely numerical on that number, least to greatest.
Non-matching lines (no EFTA########) go to the end, in original order.
"""

import re
from collections import Counter

def extract_number(line):
    """Extract ONLY the numeric portion after EFTA######## — ignore path/extension/everything else"""
    match = re.search(r'EFTA(\d{8})', line)
    if match:
        return int(match.group(1))
    return None

def main():
    input_file = 'HASHES.txt'
    output_file = 'HASHES2.txt'
    
    print("Reading HASHES.txt...")
    
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = [line.rstrip('\n\r') for line in f]
    
    print(f"Total lines read: {len(lines)}")
    
    # Duplicate check
    print("\n=== CHECKING FOR DUPLICATE LINES ===")
    line_counts = Counter(lines)
    duplicates = {line: count for line, count in line_counts.items() if count > 1}
    if duplicates:
        print(f"Found {len(duplicates)} duplicate lines (showing first 10):")
        for line, count in sorted(duplicates.items())[:10]:
            print(f"  ({count}x) {line[:80]}...")
    else:
        print("No duplicates found.")
    
    # Order check
    print("\n=== CHECKING NUMERIC ORDER ===")
    prev_num = None
    first_out_of_order = None
    out_of_order_count = 0
    for i, line in enumerate(lines, 1):
        num = extract_number(line)
        if num is None:
            print(f"Warning: Line {i} has no EFTA number: {line[:80]}")
            continue
        if prev_num is not None and num < prev_num:
            out_of_order_count += 1
            if first_out_of_order is None:
                first_out_of_order = i
                print(f"First out-of-order at line {i}:")
                print(f"  Prev: EFTA{prev_num:08d}")
                print(f"  Curr: EFTA{num:08d}")
        prev_num = num
    print(f"Out-of-order entries: {out_of_order_count}")
    
    # Deduplicate + sort by number only
    print("\n=== CREATING SORTED HASHES2.txt ===")
    seen = set()
    unique_lines = [line for line in lines if not (line in seen or seen.add(line))]
    
    def sort_key(line):
        num = extract_number(line)
        return num if num is not None else float('inf')   # non-EFTA lines go to end
    
    sorted_lines = sorted(unique_lines, key=sort_key)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        for line in sorted_lines:
            f.write(line + '\n')
    
    print(f"Created {output_file} → {len(sorted_lines)} entries (removed {len(lines)-len(unique_lines)} dups)")
    
    print("\n=== FIRST 10 LINES OF HASHES2.txt ===")
    for i, line in enumerate(sorted_lines[:10], 1):
        print(f"{i:3d}: {line[:80]}")
    
    print("\n=== SUMMARY ===")
    print(f"Original: {len(lines)} lines")
    print(f"Unique:   {len(unique_lines)}")
    print(f"Out-of-order: {out_of_order_count}")
    print(f"Sorted file: {output_file}")

if __name__ == '__main__':
    main()