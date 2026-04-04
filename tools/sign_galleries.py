#!/usr/bin/env python3
"""
tools/sign_galleries.py - Utility to sign existing galleries with HMAC

This tool is used to:
1. Resign all galleries after security update (CR-1)
2. Validate gallery signatures
3. Recover from corrupted/tampered galleries

Usage:
    python3 tools/sign_galleries.py resign    # Resign all galleries
    python3 tools/sign_galleries.py validate  # Validate all galleries
    python3 tools/sign_galleries.py status    # Status of galleries
"""

import os
import sys
import hmac
import hashlib
import numpy as np
import argparse
from pathlib import Path

def get_gallery_signing_key():
    """Gets the same signing key as used by FaceEmbeddingStore."""
    try:
        with open('/etc/machine-id', 'r') as f:
            machine_id = f.read().strip()
            return machine_id.encode() + b'_SENTINEL_GALLERY_v1'
    except:
        return b'SENTINEL_DEFAULT_KEY_v1'

def compute_signature(gallery_data):
    """Computes HMAC-SHA256 signature."""
    key = get_gallery_signing_key()
    gallery_bytes = gallery_data.tobytes()
    return hmac.new(key, gallery_bytes, hashlib.sha256).hexdigest()

def resign_galleries(gallery_dir):
    """Resigns all galleries in directory."""
    gallery_files = sorted(Path(gallery_dir).glob("gallery_*.npy"))
    
    if not gallery_files:
        print(f"No galleries found in {gallery_dir}")
        return
    
    resigned_count = 0
    for gallery_file in gallery_files:
        try:
            # Load gallery
            embeddings = np.load(gallery_file)
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)
            if embeddings.ndim != 2:
                print(f"SKIP {gallery_file}: Invalid shape {embeddings.shape}")
                continue
            
            # Compute and save signature
            signature = compute_signature(embeddings)
            sig_file = gallery_file.with_suffix('.sig')
            with open(sig_file, 'w') as f:
                f.write(signature)
            
            print(f"✓ Signed {gallery_file.name}")
            resigned_count += 1
            
        except Exception as e:
            print(f"✗ Error signing {gallery_file.name}: {e}")
    
    print(f"\nTotal resigned: {resigned_count}/{len(gallery_files)}")

def validate_galleries(gallery_dir):
    """Validates all gallery signatures."""
    gallery_files = sorted(Path(gallery_dir).glob("gallery_*.npy"))
    
    if not gallery_files:
        print(f"No galleries found in {gallery_dir}")
        return
    
    valid_count = 0
    invalid_count = 0
    missing_sig_count = 0
    
    for gallery_file in gallery_files:
        try:
            # Load gallery
            embeddings = np.load(gallery_file)
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)
            
            # Check signature
            sig_file = gallery_file.with_suffix('.sig')
            if not sig_file.exists():
                print(f"⚠ {gallery_file.name}: No signature file (legacy gallery)")
                missing_sig_count += 1
                continue
            
            with open(sig_file, 'r') as f:
                stored_sig = f.read().strip()
            
            computed_sig = compute_signature(embeddings)
            if computed_sig == stored_sig:
                print(f"✓ {gallery_file.name}: Valid signature")
                valid_count += 1
            else:
                print(f"✗ {gallery_file.name}: Invalid signature (POSSIBLE TAMPERING)")
                invalid_count += 1
                
        except Exception as e:
            print(f"✗ {gallery_file.name}: Error validating {e}")
            invalid_count += 1
    
    print(f"\nValid: {valid_count}, Invalid: {invalid_count}, Legacy (no sig): {missing_sig_count}")
    return invalid_count == 0  # Return False if any invalid signatures

def status_galleries(gallery_dir):
    """Shows status of all galleries."""
    gallery_files = sorted(Path(gallery_dir).glob("gallery_*.npy"))
    
    if not gallery_files:
        print(f"No galleries found in {gallery_dir}")
        return
    
    print(f"Gallery Directory: {gallery_dir}\n")
    print("User                 Samples  Size    Signed")
    print("-" * 50)
    
    for gallery_file in gallery_files:
        try:
            user_name = gallery_file.stem.replace("gallery_", "")
            embeddings = np.load(gallery_file)
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)
            
            num_samples = embeddings.shape[0]
            size_kb = embeddings.nbytes / 1024
            
            sig_file = gallery_file.with_suffix('.sig')
            has_sig = "yes" if sig_file.exists() else "no"
            
            print(f"{user_name:20} {num_samples:7}  {size_kb:6.1f}KB  {has_sig}")
            
        except Exception as e:
            print(f"Error reading {gallery_file.name}: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="Project Sentinel Gallery Signing Tool (CR-1 Security Update)"
    )
    parser.add_argument(
        'action',
        choices=['resign', 'validate', 'status'],
        help='Action to perform'
    )
    parser.add_argument(
        '--gallery-dir',
        default='models',
        help='Gallery directory (default: models)'
    )
    
    args = parser.parse_args()
    
    gallery_dir = Path(args.gallery_dir)
    if not gallery_dir.exists():
        print(f"Error: Gallery directory not found: {gallery_dir}")
        sys.exit(1)
    
    if args.action == 'resign':
        print("Resigning galleries with HMAC-SHA256 (CR-1)...\n")
        resign_galleries(gallery_dir)
    elif args.action == 'validate':
        print("Validating gallery signatures (CR-1)...\n")
        if not validate_galleries(gallery_dir):
            sys.exit(2)  # Exit with error if invalid signatures found
    elif args.action == 'status':
        status_galleries(gallery_dir)

if __name__ == '__main__':
    main()
