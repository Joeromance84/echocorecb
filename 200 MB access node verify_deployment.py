# verify_deployment.py
"""
Deployment Verification Script
Tests the full request flow: Access Node → Developer Tower → Artifact Storage
"""

import requests
import json
import sys
from pathlib import Path

# Configuration - Update these if needed
ACCESS_NODE_URL = "http://localhost:8000"
# This API key must match the one in your .env file and ledger.py
API_KEY = "static_access_key_compiled_into_apk"

def test_health_checks():
    """Test that both services are responsive."""
    print("🧪 Testing health checks...")
    
    try:
        # Test Access Node health
        response = requests.get(f"{ACCESS_NODE_URL}/health", timeout=5)
        response.raise_for_status()
        print("✅ Access Node health check passed")
        
        # Test basic API endpoint
        response = requests.get(f"{ACCESS_NODE_URL}/", timeout=5)
        response.raise_for_status()
        print("✅ Access Node API endpoint responsive")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Health check failed: {e}")
        return False
    
    return True

def test_python_execution():
    """Test Python code execution."""
    print("\n🧪 Testing Python execution...")
    
    payload = {
        "action": "run_python",
        "intent": {
            "code": "print('Hello from verification script!')\nresult = 2 + 2\nprint(f'2 + 2 = {result}')"
        }
    }
    
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            f"{ACCESS_NODE_URL}/api/execute",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        
        if response.status_code == 200:
            print(f"✅ Python execution request successful")
            print(f"   stdout: {result['result']['stdout'].strip()}")
            if result["result"]["stderr"]:
                print(f"   stderr: {result['result']['stderr'].strip()}")
            print(f"   return code: {result['result']['return_code']}")
            return True
        else:
            print(f"❌ Execution failed: {result['detail']}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ API request failed: {e}")
        return False

def test_shell_execution():
    """Test shell command execution."""
    print("\n🧪 Testing shell execution...")
    
    payload = {
        "action": "run_shell",
        "intent": {
            "command": "echo 'Hello from shell' && python3 --version"
        }
    }
    
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            f"{ACCESS_NODE_URL}/api/execute",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        print(f"✅ Shell execution request successful")
        
        if response.status_code == 200:
            print(f"   stdout: {result['result']['stdout'].strip()}")
            if result["result"]["stderr"]:
                print(f"   stderr: {result['result']['stderr'].strip()}")
            print(f"   return code: {result['result']['return_code']}")
            return True
        else:
            print(f"❌ Execution failed: {result['detail']}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ API request failed: {e}")
        return False

def test_artifact_storage():
    """Test artifact storage and retrieval."""
    print("\n🧪 Testing artifact storage...")
    
    # First, store an artifact
    store_payload = {
        "action": "store_artifact",
        "intent": {
            "name": "verification_test.txt",
            "content": "This is a test artifact created by the verification script."
        }
    }
    
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        # Store artifact
        response = requests.post(
            f"{ACCESS_NODE_URL}/api/execute",
            headers=headers,
            json=store_payload,
            timeout=30
        )
        response.raise_for_status()
        store_result = response.json()
        
        # Check if the store operation was successful
        if "success" not in store_result or not store_result["success"]:
            print(f"❌ Artifact storage failed: {store_result.get('error', 'Unknown error')}")
            return False
        
        print(f"✅ Artifact stored successfully")
        
        # Now retrieve the artifact using the same name
        retrieve_payload = {
            "action": "retrieve_artifact",
            "intent": {
                "name": "verification_test.txt"
            }
        }
        
        response = requests.post(
            f"{ACCESS_NODE_URL}/api/execute",
            headers=headers,
            json=retrieve_payload,
            timeout=30
        )
        response.raise_for_status()
        retrieve_result = response.json()
        
        if "success" in retrieve_result and retrieve_result["success"]:
            content = retrieve_result["result"]["content"]
            print(f"✅ Artifact retrieved successfully")
            print(f"   Content: {content}")
            return True
        else:
            print(f"❌ Artifact retrieval failed: {retrieve_result.get('error', 'Unknown error')}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ API request failed: {e}")
        return False

def test_system_info():
    """Test system information retrieval."""
    print("\n🧪 Testing system info...")
    
    payload = {
        "action": "get_system_info",
        "intent": {}
    }
    
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            f"{ACCESS_NODE_URL}/api/execute",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        print(f"✅ System info request successful")
        
        if "success" in result and result["success"]:
            # Check for the presence of key fields
            info = result["result"]
            assert "os_info" in info
            assert "cpu_info" in info
            assert "memory_info" in info
            assert "disk_info" in info
            
            print(f"   OS: {info['os_info']['system']}")
            print(f"   CPU Cores: {info['cpu_info']['cores_logical']}")
            print(f"   Memory: {info['memory_info']['total_bytes'] / (1024**3):.1f} GB")
            return True
        else:
            print(f"❌ System info failed: {result.get('error', 'Unknown error')}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ API request failed: {e}")
        return False

def test_permission_denied():
    """Test that unauthorized actions are properly rejected."""
    print("\n🧪 Testing permission enforcement...")
    
    payload = {
        "action": "unauthorized_action",
        "intent": {}
    }
    
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            f"{ACCESS_NODE_URL}/api/execute",
            headers=headers,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 403:
            print("✅ Permission enforcement working correctly")
            return True
        else:
            print(f"❌ Expected 403 Forbidden, got {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Permission test failed: {e}")
        return False

def main():
    """Run all verification tests."""
    print("🚀 Starting Deployment Verification")
    print("=" * 50)
    
    tests = [
        test_health_checks,
        test_python_execution,
        test_shell_execution,
        test_artifact_storage,
        test_system_info,
        test_permission_denied
    ]
    
    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"❌ Test crashed: {e}")
            results.append(False)
    
    print("\n" + "=" * 50)
    print("📊 Verification Results:")
    print(f"   Tests passed: {sum(results)}/{len(results)}")
    
    if all(results):
        print("🎉 All tests passed! Deployment is successful!")
        return 0
    else:
        print("❌ Some tests failed. Check the deployment.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

