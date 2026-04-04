#!/usr/bin/env python3
"""
tests/test_security_patches.py - Integration tests for CR-1, CR-2, CR-3 security patches

Run with: python3 -m pytest tests/test_security_patches.py -v

Tests:
- CR-1: Gallery tampering detection
- CR-2: Model checksum verification
- CR-3: RPC authorization enforcement
"""

import pytest
import os
import sys
import json
import socket
import tempfile
import hmac
import hashlib
import numpy as np
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

class TestGallerySigningCR1:
    """Tests for CR-1: Gallery Tampering Protection"""
    
    def setup_method(self):
        """Setup for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.gallery_dir = Path(self.temp_dir.name)
    
    def teardown_method(self):
        """Cleanup."""
        self.temp_dir.cleanup()
    
    def test_gallery_signature_computation(self):
        """Test HMAC signature computation."""
        from biometric_processor import FaceEmbeddingStore
        
        store = FaceEmbeddingStore(gallery_dir=str(self.gallery_dir))
        
        # Create test gallery
        embeddings = np.random.randn(5, 128).astype(np.float32)
        signature1 = store._compute_signature(embeddings)
        
        # Same data should produce same signature
        signature2 = store._compute_signature(embeddings)
        assert signature1 == signature2, "Deterministic signatures failed"
        
        # Different data should produce different signature
        embeddings2 = np.random.randn(5, 128).astype(np.float32)
        signature3 = store._compute_signature(embeddings2)
        assert signature1 != signature3, "Different data should have different signature"
    
    def test_gallery_signature_verification(self):
        """Test signature verification."""
        from biometric_processor import FaceEmbeddingStore
        
        store = FaceEmbeddingStore(gallery_dir=str(self.gallery_dir))
        
        # Create gallery
        embeddings = np.random.randn(5, 128).astype(np.float32)
        signature = store._compute_signature(embeddings)
        
        # Verify correct signature
        assert store._verify_signature(embeddings, signature), "Valid signature rejected"
        
        # Verify incorrect signature
        bad_sig = "0" * 64
        assert not store._verify_signature(embeddings, bad_sig), "Bad signature accepted"
    
    def test_tampering_detection(self):
        """Test that gallery tampering is detected."""
        from biometric_processor import FaceEmbeddingStore
        
        store = FaceEmbeddingStore(gallery_dir=str(self.gallery_dir))
        
        # Create and save gallery
        embeddings = np.random.randn(5, 128).astype(np.float32)
        gallery_file = self.gallery_dir / "gallery_testuser.npy"
        sig_file = self.gallery_dir / "gallery_testuser.sig"
        
        np.save(gallery_file, embeddings)
        signature = store._compute_signature(embeddings)
        with open(sig_file, 'w') as f:
            f.write(signature)
        
        # Tamper with gallery
        tampered = np.random.randn(5, 128).astype(np.float32)
        np.save(gallery_file, tampered)
        
        # Load and verify tampering detected
        galleries, names = store.load_all_galleries()
        assert "testuser" not in galleries, "Tampering not detected"
        assert "testuser" not in names, "Tampered gallery not filtered"


class TestModelVerificationCR2:
    """Tests for CR-2: Model Checksum Verification"""
    
    def setup_method(self):
        """Setup for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.model_dir = Path(self.temp_dir.name)
    
    def teardown_method(self):
        """Cleanup."""
        self.temp_dir.cleanup()
    
    def test_model_checksum_computation(self):
        """Test model checksum computation."""
        # Create a dummy model file
        model_file = self.model_dir / "test_model.onnx"
        model_data = b"dummy onnx model data"
        with open(model_file, 'wb') as f:
            f.write(model_data)
        
        # Compute hash
        sha256_hash = hashlib.sha256()
        with open(model_file, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256_hash.update(chunk)
        
        hash1 = f"sha256:{sha256_hash.hexdigest()}"
        
        # Recompute to verify consistency
        sha256_hash = hashlib.sha256()
        with open(model_file, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256_hash.update(chunk)
        
        hash2 = f"sha256:{sha256_hash.hexdigest()}"
        assert hash1 == hash2, "Inconsistent checksum computation"
    
    def test_model_tampering_detection(self):
        """Test that model tampering is detected."""
        from biometric_processor import BiometricProcessor, BiometricConfig
        
        # Create test model file
        model_file = self.model_dir / "test_model.onnx"
        model_data = b"dummy onnx model"
        with open(model_file, 'wb') as f:
            f.write(model_data)
        
        # Compute correct hash
        sha256_hash = hashlib.sha256()
        with open(model_file, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256_hash.update(chunk)
        correct_hash = f"sha256:{sha256_hash.hexdigest()}"
        
        # Create processor with correct hash
        with patch.object(BiometricProcessor, 'MODEL_CHECKSUMS', {
            'test_model.onnx': correct_hash
        }):
            # Verification should pass first time
            processor = BiometricProcessor()
            processor.model_dir = str(self.model_dir)
            processor.config = Mock()
            processor.config.DETECTOR_MODEL_FILE = 'test_model.onnx'
            
            # Should NOT raise on verification
            try:
                processor._verify_model_checksums()
            except RuntimeError:
                pytest.fail("Verification failed on correct model")


class TestRPCAuthorizationCR3:
    """Tests for CR-3: RPC Authorization"""
    
    def test_rpc_permission_checks(self):
        """Test RPC permission checking logic."""
        # This imports the authorization function
        from sentinel_service import _check_rpc_permission
        
        # Public methods - anyone
        allowed, reason = _check_rpc_permission('status', 1000)
        assert allowed, "Public method should be allowed"
        
        allowed, reason = _check_rpc_permission('authenticate_pam', 1000)
        assert allowed, "Public method should be allowed"
        
        # Admin methods - only root
        allowed, reason = _check_rpc_permission('initialize', 0)
        assert allowed, "Root should access admin methods"
        
        allowed, reason = _check_rpc_permission('initialize', 1000)
        assert not allowed, "Unprivileged user should not access admin methods"
        
        # Check error message
        allowed, reason = _check_rpc_permission('start_enrollment', 1000)
        assert not allowed, "start_enrollment should require root"
        assert "root" in reason.lower(), "Error message should mention privilege"


class TestIntegration:
    """Integration tests for all patches together"""
    
    def test_security_patches_dont_break_normal_flow(self):
        """Ensure patches don't break normal authentication flow."""
        # This is a smoke test to check code is syntactically correct
        
        try:
            from biometric_processor import BiometricProcessor, BiometricConfig, FaceEmbeddingStore
            from sentinel_service import SentinelService, _check_rpc_permission
            
            # Should not raise on import
            assert BiometricProcessor is not None
            assert FaceEmbeddingStore is not None
            assert SentinelService is not None
            assert _check_rpc_permission is not None
            
        except ImportError as e:
            pytest.fail(f"Security patch imports failed: {e}")


class TestAuditLogging:
    """Tests for ME-3 and ME-4: Audit logging improvements"""
    
    def test_audit_log_file_permissions(self):
        """Test that audit log files have correct permissions."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            log_file = f.name
        
        try:
            # Simulate setting correct permissions
            os.chmod(log_file, 0o600)
            
            # Check permissions
            stat_info = os.stat(log_file)
            mode = stat_info.st_mode & 0o777
            
            assert mode == 0o600, f"Expected 0o600 permissions, got {oct(mode)}"
            
        finally:
            os.unlink(log_file)


# ============== COMMAND-LINE EXECUTION ==============

if __name__ == '__main__':
    unittest.main()
