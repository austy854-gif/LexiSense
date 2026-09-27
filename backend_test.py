#!/usr/bin/env python3
"""
Backend Post-Launch Verification Tests for LexiSense
Tests all 6 gates against http://localhost:8001
"""
import asyncio
import aiohttp
import json
import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load environment
load_dotenv('/app/backend/.env')

BASE_URL = "http://localhost:8001"
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'lexisense')

# Test credentials
TEST_EMAIL = "demo@lexisense.com"
TEST_PASSWORD = "Demo1234!"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def print_test(name, passed, reason=""):
    status = f"{Colors.GREEN}✅ PASS{Colors.END}" if passed else f"{Colors.RED}❌ FAIL{Colors.END}"
    print(f"{status} - {name}")
    if reason:
        print(f"  Reason: {reason}")
    print()

async def test_1_health_endpoint(session):
    """Test 1: Health endpoint returns correct structure"""
    print(f"\n{Colors.BLUE}=== TEST 1: Health Endpoint ==={Colors.END}")
    
    try:
        async with session.get(f"{BASE_URL}/api/v1/health") as resp:
            if resp.status != 200:
                print_test("Health endpoint", False, f"Status {resp.status}, expected 200")
                return False
            
            data = await resp.json()
            
            # Check required fields
            required_fields = ['status', 'version', 'checks']
            missing = [f for f in required_fields if f not in data]
            if missing:
                print_test("Health endpoint", False, f"Missing fields: {missing}")
                return False
            
            # Check checks structure
            checks = data.get('checks', {})
            required_checks = ['database', 'ai', 'storage', 'email']
            missing_checks = [c for c in required_checks if c not in checks]
            if missing_checks:
                print_test("Health endpoint", False, f"Missing checks: {missing_checks}")
                return False
            
            # Verify database is healthy
            db_status = checks.get('database', {}).get('status')
            if db_status != 'healthy':
                print_test("Health endpoint", False, f"Database status: {db_status}, expected 'healthy'")
                return False
            
            # Verify AI is healthy or degraded (not unhealthy)
            ai_status = checks.get('ai', {}).get('status')
            if ai_status not in ['healthy', 'degraded']:
                print_test("Health endpoint", False, f"AI status: {ai_status}, expected 'healthy' or 'degraded'")
                return False
            
            # Storage and email can be degraded (mock is OK)
            storage_status = checks.get('storage', {}).get('status')
            email_status = checks.get('email', {}).get('status')
            
            print(f"  Status: {data.get('status')}")
            print(f"  Version: {data.get('version')}")
            print(f"  Database: {db_status}")
            print(f"  AI: {ai_status}")
            print(f"  Storage: {storage_status} (degraded/mock OK)")
            print(f"  Email: {email_status} (degraded OK)")
            
            print_test("Health endpoint", True, "All required fields present and database healthy")
            return True
            
    except Exception as e:
        print_test("Health endpoint", False, f"Exception: {str(e)}")
        return False

async def test_2_route_prefix_consistency(session):
    """Test 2: Route prefix consistency - all routes under /api/v1"""
    print(f"\n{Colors.BLUE}=== TEST 2: Route Prefix Consistency ==={Colors.END}")
    
    all_passed = True
    
    # Test 2a: /api/v1/ should return 200
    try:
        async with session.get(f"{BASE_URL}/api/v1/") as resp:
            if resp.status == 200:
                data = await resp.json()
                print_test("GET /api/v1/ returns 200", True, f"Message: {data.get('message')}")
            else:
                print_test("GET /api/v1/ returns 200", False, f"Status {resp.status}")
                all_passed = False
    except Exception as e:
        print_test("GET /api/v1/ returns 200", False, f"Exception: {str(e)}")
        all_passed = False
    
    # Test 2b: /api/health (no v1) should return 404
    try:
        async with session.get(f"{BASE_URL}/api/health") as resp:
            if resp.status == 404:
                print_test("GET /api/health (no v1) returns 404", True, "Correctly not found")
            else:
                print_test("GET /api/health (no v1) returns 404", False, f"Status {resp.status}, expected 404")
                all_passed = False
    except Exception as e:
        print_test("GET /api/health (no v1) returns 404", False, f"Exception: {str(e)}")
        all_passed = False
    
    # Test 2c: Check OpenAPI spec for forbidden routes
    try:
        # Try /api/v1/openapi.json first
        async with session.get(f"{BASE_URL}/api/v1/openapi.json") as resp:
            if resp.status == 404:
                # Try /api/openapi.json
                async with session.get(f"{BASE_URL}/api/openapi.json") as resp2:
                    if resp2.status != 200:
                        print_test("OpenAPI spec accessible", False, f"Neither /api/v1/openapi.json nor /api/openapi.json returned 200")
                        all_passed = False
                    else:
                        openapi_data = await resp2.json()
                        paths = openapi_data.get('paths', {})
                        
                        # Check for forbidden routes
                        forbidden_patterns = ['/agentic', '/twofa', '/tags', '/reports']
                        found_forbidden = []
                        for path in paths.keys():
                            for pattern in forbidden_patterns:
                                if pattern in path:
                                    found_forbidden.append(path)
                        
                        if found_forbidden:
                            print_test("No forbidden routes in OpenAPI", False, f"Found: {found_forbidden}")
                            all_passed = False
                        else:
                            print_test("No forbidden routes in OpenAPI", True, "No /agentic, /twofa, /tags, or /reports routes")
            else:
                openapi_data = await resp.json()
                paths = openapi_data.get('paths', {})
                
                # Check for forbidden routes
                forbidden_patterns = ['/agentic', '/twofa', '/tags', '/reports']
                found_forbidden = []
                for path in paths.keys():
                    for pattern in forbidden_patterns:
                        if pattern in path:
                            found_forbidden.append(path)
                
                if found_forbidden:
                    print_test("No forbidden routes in OpenAPI", False, f"Found: {found_forbidden}")
                    all_passed = False
                else:
                    print_test("No forbidden routes in OpenAPI", True, "No /agentic, /twofa, /tags, or /reports routes")
    except Exception as e:
        print_test("OpenAPI spec check", False, f"Exception: {str(e)}")
        all_passed = False
    
    return all_passed

async def test_3_auth(session):
    """Test 3: Auth with demo user"""
    print(f"\n{Colors.BLUE}=== TEST 3: Authentication ==={Colors.END}")
    
    try:
        login_data = {
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        }
        
        async with session.post(f"{BASE_URL}/api/v1/auth/login", json=login_data) as resp:
            if resp.status != 200:
                text = await resp.text()
                print_test("Login with demo user", False, f"Status {resp.status}, response: {text}")
                return None
            
            data = await resp.json()
            token = data.get('access_token')
            
            if not token:
                print_test("Login with demo user", False, "No access_token in response")
                return None
            
            print_test("Login with demo user", True, f"Token received (length: {len(token)})")
            return token
            
    except Exception as e:
        print_test("Login with demo user", False, f"Exception: {str(e)}")
        return None

async def test_4_atomic_trial_quota(session, token):
    """Test 4: Atomic trial quota (Gate 4) - the critical concurrency test"""
    print(f"\n{Colors.BLUE}=== TEST 4: Atomic Trial Quota (Gate 4) ==={Colors.END}")
    
    if not token:
        print_test("Atomic trial quota", False, "No auth token available")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    all_passed = True
    
    # Step 1: Get initial subscription state
    try:
        async with session.get(f"{BASE_URL}/api/v1/billing/subscription", headers=headers) as resp:
            if resp.status != 200:
                print_test("Get subscription", False, f"Status {resp.status}")
                return False
            
            sub_data = await resp.json()
            initial_used = sub_data.get('trialContractsUsed', 0)
            limit = sub_data.get('trialContractsLimit', 3)
            
            print(f"  Initial state: {initial_used}/{limit} contracts used")
            
            if limit != 3:
                print_test("Trial limit is 3", False, f"Limit is {limit}, expected 3")
                all_passed = False
            else:
                print_test("Trial limit is 3", True)
    except Exception as e:
        print_test("Get subscription", False, f"Exception: {str(e)}")
        return False
    
    # Step 2: Upload one contract if we have room
    if initial_used < limit:
        try:
            # Create a test file
            test_content = "This is a test contract between Party A and Party B for testing purposes."
            
            form_data = aiohttp.FormData()
            form_data.add_field('title', 'Concurrency Test')
            form_data.add_field('contractType', 'Service Agreement')
            form_data.add_field('file', test_content, filename='test_contract.txt', content_type='text/plain')
            
            async with session.post(f"{BASE_URL}/api/v1/contracts", headers=headers, data=form_data) as resp:
                if resp.status == 200:
                    # Check that quota incremented by 1
                    async with session.get(f"{BASE_URL}/api/v1/billing/subscription", headers=headers) as resp2:
                        sub_data = await resp2.json()
                        new_used = sub_data.get('trialContractsUsed', 0)
                        
                        if new_used == initial_used + 1:
                            print_test("Single upload increments quota by 1", True, f"{initial_used} -> {new_used}")
                            initial_used = new_used
                        else:
                            print_test("Single upload increments quota by 1", False, f"Expected {initial_used + 1}, got {new_used}")
                            all_passed = False
                elif resp.status == 403:
                    # Already at limit
                    print(f"  Already at limit ({initial_used}/{limit}), skipping single upload test")
                else:
                    text = await resp.text()
                    print_test("Single upload", False, f"Status {resp.status}, response: {text}")
                    all_passed = False
        except Exception as e:
            print_test("Single upload", False, f"Exception: {str(e)}")
            all_passed = False
    
    # Step 3: Concurrency test - fire 5 uploads simultaneously
    remaining = limit - initial_used
    print(f"\n  Concurrency test: firing 5 simultaneous uploads (remaining quota: {remaining})")
    
    async def upload_contract(session, headers, index):
        """Upload a single contract"""
        try:
            test_content = f"Concurrent test contract {index} between Party A and Party B for testing purposes."
            
            form_data = aiohttp.FormData()
            form_data.add_field('title', f'Concurrent Test {index}')
            form_data.add_field('contractType', 'Service Agreement')
            form_data.add_field('file', test_content, filename=f'concurrent_{index}.txt', content_type='text/plain')
            
            async with session.post(f"{BASE_URL}/api/v1/contracts", headers=headers, data=form_data) as resp:
                status = resp.status
                if status == 200:
                    data = await resp.json()
                    return {'index': index, 'status': status, 'success': True, 'id': data.get('id')}
                else:
                    text = await resp.text()
                    return {'index': index, 'status': status, 'success': False, 'message': text}
        except Exception as e:
            return {'index': index, 'status': 0, 'success': False, 'error': str(e)}
    
    # Fire 5 concurrent uploads
    tasks = [upload_contract(session, headers, i) for i in range(5)]
    results = await asyncio.gather(*tasks)
    
    successes = [r for r in results if r['success']]
    failures = [r for r in results if not r['success']]
    
    print(f"  Results: {len(successes)} succeeded, {len(failures)} failed")
    
    # Check final quota
    async with session.get(f"{BASE_URL}/api/v1/billing/subscription", headers=headers) as resp:
        sub_data = await resp.json()
        final_used = sub_data.get('trialContractsUsed', 0)
    
    print(f"  Final quota: {final_used}/{limit}")
    
    # Verify atomicity: exactly 'remaining' should have succeeded
    if len(successes) == remaining:
        print_test("Concurrency test: correct number succeeded", True, f"{remaining} uploads succeeded as expected")
    else:
        print_test("Concurrency test: correct number succeeded", False, f"Expected {remaining} successes, got {len(successes)}")
        all_passed = False
    
    # Verify final quota is exactly at limit
    if final_used == limit:
        print_test("Concurrency test: quota at limit", True, f"Final quota: {final_used}/{limit}")
    else:
        print_test("Concurrency test: quota at limit", False, f"Expected {limit}, got {final_used}")
        all_passed = False
    
    # Verify failures returned 403 with trial limit message
    for failure in failures:
        if failure['status'] != 403:
            print_test("Failed uploads return 403", False, f"Upload {failure['index']} returned {failure['status']}")
            all_passed = False
            break
    else:
        if failures:
            print_test("Failed uploads return 403", True, f"All {len(failures)} failures returned 403")
    
    # Step 4: Verify database has exactly 'limit' contracts for this org
    try:
        # Get user's org ID
        async with session.get(f"{BASE_URL}/api/v1/auth/me", headers=headers) as resp:
            user_data = await resp.json()
            org_id = user_data.get('organizationId')
        
        # Connect to MongoDB and count contracts
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client[DB_NAME]
        contract_count = await db.contracts.count_documents({"organizationId": org_id})
        
        if contract_count == limit:
            print_test("Database has exactly 3 contracts", True, f"Count: {contract_count}")
        else:
            print_test("Database has exactly 3 contracts", False, f"Expected {limit}, got {contract_count}")
            all_passed = False
        
        mongo_client.close()
    except Exception as e:
        print_test("Database contract count", False, f"Exception: {str(e)}")
        all_passed = False
    
    # Step 5: Try to upload when at limit - should fail
    try:
        test_content = "This should fail - at limit"
        
        form_data = aiohttp.FormData()
        form_data.add_field('title', 'Should Fail')
        form_data.add_field('contractType', 'Service Agreement')
        form_data.add_field('file', test_content, filename='should_fail.txt', content_type='text/plain')
        
        async with session.post(f"{BASE_URL}/api/v1/contracts", headers=headers, data=form_data) as resp:
            if resp.status == 403:
                text = await resp.text()
                if 'trial' in text.lower() or 'limit' in text.lower():
                    print_test("Upload at limit returns 403 with trial message", True)
                else:
                    print_test("Upload at limit returns 403 with trial message", False, f"Message doesn't mention trial/limit: {text}")
                    all_passed = False
            else:
                print_test("Upload at limit returns 403", False, f"Status {resp.status}, expected 403")
                all_passed = False
    except Exception as e:
        print_test("Upload at limit", False, f"Exception: {str(e)}")
        all_passed = False
    
    return all_passed

async def test_5_ai_error_handling(session, token):
    """Test 5: AI error handling (Gate 1)"""
    print(f"\n{Colors.BLUE}=== TEST 5: AI Error Handling (Gate 1) ==={Colors.END}")
    
    if not token:
        print_test("AI error handling", False, "No auth token available")
        return False
    
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        # Get list of contracts
        async with session.get(f"{BASE_URL}/api/v1/contracts", headers=headers) as resp:
            if resp.status != 200:
                print_test("Get contracts", False, f"Status {resp.status}")
                return False
            
            data = await resp.json()
            # Handle both list and dict responses
            if isinstance(data, list):
                contracts = data
            else:
                contracts = data.get('contracts', [])
            
            if not contracts:
                print_test("AI error handling", False, "No contracts to check")
                return False
            
            # Check first contract
            contract_id = contracts[0].get('id')
            
            async with session.get(f"{BASE_URL}/api/v1/contracts/{contract_id}", headers=headers) as resp2:
                if resp2.status != 200:
                    print_test("Get contract detail", False, f"Status {resp2.status}")
                    return False
                
                contract = await resp2.json()
                
                # Check AI analysis status
                ai_status = contract.get('aiAnalysisStatus')
                ai_analysis = contract.get('aiAnalysis')
                ai_error = contract.get('aiAnalysisError')
                
                print(f"  Contract ID: {contract_id}")
                print(f"  AI Status: {ai_status}")
                print(f"  AI Analysis: {'Present' if ai_analysis else 'None'}")
                print(f"  AI Error: {ai_error if ai_error else 'None'}")
                
                # Must have EITHER success with analysis OR failed with error
                if ai_status == 'success' and ai_analysis:
                    print_test("AI error handling", True, "Contract has aiAnalysisStatus='success' with analysis")
                    return True
                elif ai_status == 'failed' and ai_error:
                    print_test("AI error handling", True, "Contract has aiAnalysisStatus='failed' with error message")
                    return True
                else:
                    print_test("AI error handling", False, f"Silent-null analysis detected: status={ai_status}, analysis={bool(ai_analysis)}, error={bool(ai_error)}")
                    return False
    except Exception as e:
        print_test("AI error handling", False, f"Exception: {str(e)}")
        return False

async def test_6_migrations(session):
    """Test 6: Migrations (Gate 6)"""
    print(f"\n{Colors.BLUE}=== TEST 6: Migrations (Gate 6) ==={Colors.END}")
    
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client[DB_NAME]
        
        # Check schema_migrations collection
        migrations = await db.schema_migrations.find({}).to_list(length=100)
        
        if not migrations:
            print_test("Migrations collection exists", False, "No migrations found")
            mongo_client.close()
            return False
        
        print(f"  Found {len(migrations)} migrations")
        
        # Check for required migrations
        required_migrations = {
            1: 'backfill_billing_trial_limits',
            2: 'add_ai_analysis_status'
        }
        
        found_migrations = {}
        for migration in migrations:
            version = migration.get('version')
            name = migration.get('name')
            found_migrations[version] = name
            print(f"  Migration {version}: {name}")
        
        all_found = True
        for version, expected_name in required_migrations.items():
            if version not in found_migrations:
                print_test(f"Migration {version} exists", False, f"Not found")
                all_found = False
            elif found_migrations[version] != expected_name:
                print_test(f"Migration {version} name", False, f"Expected '{expected_name}', got '{found_migrations[version]}'")
                all_found = False
            else:
                print_test(f"Migration {version}: {expected_name}", True)
        
        mongo_client.close()
        return all_found
        
    except Exception as e:
        print_test("Migrations check", False, f"Exception: {str(e)}")
        return False

async def main():
    """Run all tests"""
    print(f"\n{Colors.BLUE}{'='*60}")
    print("LexiSense Backend Post-Launch Verification")
    print(f"{'='*60}{Colors.END}\n")
    
    results = {}
    
    async with aiohttp.ClientSession() as session:
        # Test 1: Health endpoint
        results['test_1_health'] = await test_1_health_endpoint(session)
        
        # Test 2: Route prefix consistency
        results['test_2_routes'] = await test_2_route_prefix_consistency(session)
        
        # Test 3: Auth
        token = await test_3_auth(session)
        results['test_3_auth'] = token is not None
        
        # Test 4: Atomic trial quota (requires auth)
        if token:
            results['test_4_quota'] = await test_4_atomic_trial_quota(session, token)
        else:
            results['test_4_quota'] = False
            print(f"{Colors.RED}Skipping Test 4 (no auth token){Colors.END}")
        
        # Test 5: AI error handling (requires auth)
        if token:
            results['test_5_ai'] = await test_5_ai_error_handling(session, token)
        else:
            results['test_5_ai'] = False
            print(f"{Colors.RED}Skipping Test 5 (no auth token){Colors.END}")
        
        # Test 6: Migrations
        results['test_6_migrations'] = await test_6_migrations(session)
    
    # Summary
    print(f"\n{Colors.BLUE}{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}{Colors.END}\n")
    
    test_names = {
        'test_1_health': 'Test 1: Health Endpoint',
        'test_2_routes': 'Test 2: Route Prefix Consistency',
        'test_3_auth': 'Test 3: Authentication',
        'test_4_quota': 'Test 4: Atomic Trial Quota (Gate 4)',
        'test_5_ai': 'Test 5: AI Error Handling (Gate 1)',
        'test_6_migrations': 'Test 6: Migrations (Gate 6)'
    }
    
    for key, name in test_names.items():
        passed = results.get(key, False)
        status = f"{Colors.GREEN}✅ PASS{Colors.END}" if passed else f"{Colors.RED}❌ FAIL{Colors.END}"
        print(f"{status} - {name}")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    
    print(f"\n{Colors.BLUE}Total: {passed}/{total} tests passed{Colors.END}\n")
    
    return all(results.values())

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
