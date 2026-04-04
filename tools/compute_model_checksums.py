#!/usr/bin/env python3
"""
tools/compute_model_checksums.py - Compute and verify model checksums (CR-2)

This tool:
1. Computes SHA256 hashes of all ONNX model files
2. Generates the MODEL_CHECKSUMS dict for biometric_processor.py
3. Verifies model integrity

Usage:
    python3 tools/compute_model_checksums.py generate   # Generate checksums
    python3 tools/compute_model_checksums.py verify      # Verify checksums
"""

import os
import sys
import hashlib
import json
import re
from pathlib import Path

def compute_file_hash(file_path):
    """Computes SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                sha256_hash.update(byte_block)
        return f"sha256:{sha256_hash.hexdigest()}"
    except Exception as e:
        print(f"Error computing hash for {file_path}: {e}")
        return None

def generate_checksums(model_dir):
    """Generates SHA256 checksums for all models."""
    model_dir = Path(model_dir)
    models = list(model_dir.glob("*.onnx"))
    
    if not models:
        print(f"No ONNX models found in {model_dir}")
        return {}
    
    checksums = {}
    print("Computing model checksums (CR-2)...\n")
    
    for model_file in sorted(models):
        print(f"Hashing {model_file.name}...", end=" ", flush=True)
        hash_val = compute_file_hash(model_file)
        if hash_val:
            checksums[model_file.name] = hash_val
            print(f"OK")
            print(f"  → {hash_val[:50]}...")
        else:
            print("FAILED")
    
    # Generate Python dict snippet for biometric_processor.py
    print("\n" + "="*70)
    print("Add this to core/biometric_processor.py in BiometricProcessor class:")
    print("="*70)
    
    dict_str = "MODEL_CHECKSUMS = {\n"
    for name, hash_val in checksums.items():
        dict_str += f"    '{name}':\n"
        dict_str += f"        '{hash_val}',\n"
    dict_str += "}\n"
    
    print(dict_str)
    
    # Also save to JSON for reference
    checksum_file = model_dir / "CHECKSUMS.json"
    with open(checksum_file, 'w') as f:
        json.dump(checksums, f, indent=2)
    print(f"\nChecksums also saved to: {checksum_file}\n")
    
    return checksums

def verify_checksums(model_dir, checksums_dict):
    """Verifies model files against checksums."""
    model_dir = Path(model_dir)
    
    print("Verifying model checksums (CR-2)...\n")
    
    all_valid = True
    for model_name, expected_hash in checksums_dict.items():
        model_path = model_dir / model_name
        
        if not model_path.exists():
            print(f"✗ Missing: {model_name}")
            all_valid = False
            continue
        
        print(f"Checking {model_name}...", end=" ", flush=True)
        actual_hash = compute_file_hash(model_path)
        
        if actual_hash == expected_hash:
            print("✓ OK")
        else:
            print("✗ MISMATCH (possible tampering)")
            print(f"  Expected: {expected_hash}")
            print(f"  Actual:   {actual_hash}")
            all_valid = False
    
    if all_valid:
        print("\n✓ All models verified successfully")
        return True
    else:
        print("\n✗ Some models failed verification")
        return False

def extract_checksums_from_python(python_file):
    """Extracts checksums from biometric_processor.py."""
    try:
        with open(python_file, 'r') as f:
            content = f.read()
        
        # Find MODEL_CHECKSUMS dict
        match = re.search(
            r'MODEL_CHECKSUMS\s*=\s*\{([^}]+)\}',
            content,
            re.DOTALL
        )
        
        if not match:
            return {}
        
        checksum_block = match.group(1)
        checksums = {}
        
        # Parse entries
        entries = re.findall(
            r"'([^']+)':\s*'(sha256:[^']+)'",
            checksum_block
        )
        
        for model_name, hash_val in entries:
            checksums[model_name] = hash_val
        
        return checksums
        
    except Exception as e:
        print(f"Error parsing {python_file}: {e}")
        return {}

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Project Sentinel Model Checksum Tool (CR-2 Security Update)"
    )
    parser.add_argument(
        'action',
        choices=['generate', 'verify'],
        help='Action to perform'
    )
    parser.add_argument(
        '--model-dir',
        default='models',
        help='Model directory (default: models)'
    )
    parser.add_argument(
        '--biometric-file',
        default='core/biometric_processor.py',
        help='Path to biometric_processor.py'
    )
    
    args = parser.parse_args()
    
    if args.action == 'generate':
        checksums = generate_checksums(args.model_dir)
        
        # Step-by-step instructions
        print("\n" + "="*70)
        print("NEXT STEPS (CR-2 Implementation):")
        print("="*70)
        print("""
1. Copy the MODEL_CHECKSUMS dict above
2. Open core/biometric_processor.py
3. Find BiometricProcessor class
4. Replace the MODEL_CHECKSUMS dict in the class
5. Re-run: python3 tools/compute_model_checksums.py verify
6. Commit and deploy
        """)
        
    elif args.action == 'verify':
        # Try to load checksums from biometric_processor.py
        checksums = extract_checksums_from_python(args.biometric_file)
        
        if not checksums:
            print(f"Could not extract checksums from {args.biometric_file}")
            print("Skipping verification (checksums might be empty in dev mode)")
            sys.exit(1)
        
        if verify_checksums(args.model_dir, checksums):
            sys.exit(0)
        else:
            sys.exit(1)

if __name__ == '__main__':
    main()
