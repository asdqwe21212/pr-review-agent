"""Webhook 测试"""
import pytest
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from backend.webhook import verify_signature


class TestWebhookSignature:
    def test_valid_signature(self):
        """有效签名应通过验证"""
        secret = "test_secret"
        payload = b'{"action": "opened"}'
        
        expected_sig = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        
        with patch('backend.webhook.WEBHOOK_SECRET', secret):
            assert verify_signature(payload, expected_sig) is True
    
    def test_invalid_signature(self):
        """无效签名应失败"""
        secret = "test_secret"
        payload = b'{"action": "opened"}'
        
        with patch('backend.webhook.WEBHOOK_SECRET', secret):
            assert verify_signature(payload, "sha256=invalid") is False
    
    def test_missing_secret(self):
        """缺少 secret 时应拒绝所有请求"""
        with patch('backend.webhook.WEBHOOK_SECRET', ''):
            assert verify_signature(b'test', 'sig') is False