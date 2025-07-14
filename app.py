import os
import logging
import sys
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests
from flask import Flask, request, jsonify
from dataclasses import dataclass
import json

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('procore-automation')

# Configuration
PROCORE_CLIENT_ID = os.environ.get('PROCORE_CLIENT_ID')
PROCORE_CLIENT_SECRET = os.environ.get('PROCORE_CLIENT_SECRET')
PROCORE_REDIRECT_URI = os.environ.get('PROCORE_REDIRECT_URI')
PROCORE_ENVIRONMENT = os.environ.get('PROCORE_ENVIRONMENT', 'sandbox')  # sandbox or production

@dataclass
class ApprovalTier:
    """Approval tier definitions"""
    TIER_1 = 1  # Revised Contract Amount < $5,000 and Under Revised Budget
    TIER_2 = 2  # $5,000 < Revised Contract Amount < $10,000 and Under Revised Budget
    TIER_3 = 3  # Revised Contract Amount > $10,000 and under Revised Budget OR PO Line Item Budget Code is 99-999 Unallocated Costs
    TIER_4 = 4  # When Unapproved Change Order attached to PO
    TIER_5 = 5  # Committed Costs > Revised Budget

@dataclass
class ProcoreWebhookPayload:
    """Parsed webhook payload"""
    id: str
    timestamp: str
    reason: str
    company_id: str
    project_id: str
    user_id: str
    resource_type: str
    resource_id: str
    payload_version: str
    data: Optional[Dict] = None

class ProcoreAPIClient:
    """Procore API client with OAuth 2.0 authentication"""
    
    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = None
        self.environment = PROCORE_ENVIRONMENT
        
        # Set base URLs based on environment
        if self.environment == 'production':
            self.oauth_base = 'https://login.procore.com'
            self.api_base = 'https://api.procore.com'
        else:  # sandbox
            self.oauth_base = 'https://sandbox.procore.com'
            self.api_base = 'https://sandbox.procore.com'
        
    def authenticate(self, authorization_code: str = None):
        """Authenticate using OAuth 2.0 - supports both flows"""
        if authorization_code:
            # Initial authentication with authorization code
            return self._get_access_token_from_code(authorization_code)
        else:
            # Try to refresh existing token
            return self._refresh_access_token()
    
    def _get_access_token_from_code(self, code: str) -> bool:
        """Exchange authorization code for access token"""
        url = f"{self.oauth_base}/oauth/token"
        data = {
            'grant_type': 'authorization_code',
            'client_id': PROCORE_CLIENT_ID,
            'client_secret': PROCORE_CLIENT_SECRET,
            'code': code,
            'redirect_uri': PROCORE_REDIRECT_URI
        }
        
        try:
            logger.info(f"🔑 Exchanging authorization code for access token ({self.environment})...")
            response = requests.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            self.refresh_token = token_data.get('refresh_token')
            expires_in = token_data.get('expires_in', 7200)  # Usually 2 hours
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(f"✅ Successfully authenticated with Procore {self.environment} (expires in {expires_in/3600:.1f} hours)")
            logger.info(f"🔄 Refresh token available: {'Yes' if self.refresh_token else 'No'}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Authorization code authentication failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    logger.error(f"❌ Error details: {error_detail}")
                except:
                    logger.error(f"❌ Response text: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected authentication error: {e}")
            return False
    
    def _refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            logger.error("❌ No refresh token available - need to re-authenticate")
            return False
            
        url = f"{self.oauth_base}/oauth/token"
        data = {
            'grant_type': 'refresh_token',
            'client_id': PROCORE_CLIENT_ID,
            'client_secret': PROCORE_CLIENT_SECRET,
            'refresh_token': self.refresh_token
        }
        
        try:
            logger.info("🔄 Refreshing access token...")
            response = requests.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            # Refresh token might be updated
            self.refresh_token = token_data.get('refresh_token', self.refresh_token)
            expires_in = token_data.get('expires_in', 7200)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(f"✅ Successfully refreshed access token (expires in {expires_in/3600:.1f} hours)")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Token refresh failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    logger.error(f"❌ Error details: {error_detail}")
                except:
                    logger.error(f"❌ Response text: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected refresh error: {e}")
            return False
    
    def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid access token"""
        if not self.access_token:
            return False
            
        if self.token_expires_at and datetime.now() >= self.token_expires_at:
            return self._refresh_access_token()
            
        return True
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """Make authenticated API request"""
        if not self._ensure_valid_token():
            logger.error("❌ Cannot make request: no valid token")
            return None
            
        # Use appropriate API base URL for environment
        url = f"{self.api_base}/rest{endpoint}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ API request failed: {method} {endpoint} - {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    logger.error(f"❌ Error details: {error_detail}")
                except:
                    logger.error(f"❌ Response text: {e.response.text}")
            return None
    
    def get_po_line_items(self, project_id: str, po_id: str, company_id: str = None) -> Optional[List[Dict]]:
        """Get PO data using the correct Procore API endpoint"""
        # Use the correct endpoint structure with required project_id parameter
        endpoint = f"/v1.0/purchase_order_contracts/{po_id}"
        
        # Add project_id as a query parameter (required per Procore documentation)
        params = {'project_id': project_id}
        
        logger.info(f"🔍 Trying correct endpoint: {endpoint} with params: {params}")
        result = self._make_request('GET', endpoint, params=params)
        
        if result is not None:
            logger.info(f"✅ Successfully got PO data from: {endpoint}")
            
            # Check if we have line_items in the response
            if 'line_items' in result:
                logger.info(f"📝 Found {len(result['line_items'])} line items")
                return result['line_items']
            
            # If no line_items but we have an amount, create a mock line item
            elif 'amount' in result or 'total' in result or 'contract_amount' in result:
                amount = result.get('amount') or result.get('total') or result.get('contract_amount', 0)
                logger.info(f"📊 Got PO total amount: ${float(amount):,.2f}")
                return [{'amount': amount}]
            
            # If we got the PO object but no clear amount, try to extract from other fields
            else:
                logger.info(f"📋 Got PO object: {list(result.keys())}")
                # Log the structure to help debug
                for key, value in result.items():
                    if 'amount' in key.lower() or 'total' in key.lower():
                        logger.info(f"💰 Found amount field {key}: {value}")
                
                # Try common amount field names
                for amount_field in ['amount', 'total', 'contract_amount', 'grand_total', 'subtotal']:
                    if amount_field in result and result[amount_field]:
                        amount = float(result[amount_field])
                        logger.info(f"📊 Using {amount_field}: ${amount:,.2f}")
                        return [{'amount': amount}]
                
                # If still no amount found, return the raw result for debugging
                logger.warning(f"⚠️ No amount field found in PO {po_id}")
                return [{'amount': 0}]  # Return 0 to prevent None errors
        
        logger.error(f"❌ Could not retrieve PO {po_id} from endpoint {endpoint}")
        return None
    
    def get_budget_line_item(self, budget_id: str) -> Optional[Dict]:
        """Get original budget line item"""
        endpoint = f"/v1.1/budget_line_items/{budget_id}"
        return self._make_request('GET', endpoint)
    
    def get_budget_changes(self, company_id: str, project_id: str) -> Optional[List[Dict]]:
        """Get approved budget changes"""
        endpoint = f"/v2.0/companies/{company_id}/projects/{project_id}/budget_changes/adjustment_line_items"
        params = {'status': 'approved'}
        return self._make_request('GET', endpoint, params=params)
    
    def get_change_orders(self, project_id: str, status: str = None) -> Optional[List[Dict]]:
        """Get change orders (approved or unapproved)"""
        endpoint = f"/v1.0/projects/{project_id}/commitment_change_orders"
        params = {}
        if status:
            params['status'] = status
        return self._make_request('GET', endpoint, params=params)
    
    def get_po_change_orders(self, po_id: str) -> Optional[List[Dict]]:
        """Get change orders associated with a specific Purchase Order Contract"""
        endpoint = f"/v1.0/purchase_order_contracts/{po_id}"
        
        logger.info(f"🔍 Getting change orders for PO {po_id}")
        result = self._make_request('GET', endpoint)
        
        if result is not None:
            change_order_packages = result.get('change_order_packages', [])
            logger.info(f"📦 Found {len(change_order_packages)} change order packages for PO {po_id}")
            
            # Extract change orders with their status
            change_orders = []
            for package in change_order_packages:
                if isinstance(package, dict):
                    status = package.get('status', 'unknown')
                    change_orders.append({
                        'id': package.get('id'),
                        'status': status,
                        'package': package
                    })
                    logger.info(f"📋 Change Order Package {package.get('id')}: status={status}")
            
            return change_orders
        else:
            logger.error(f"❌ Failed to get change orders for PO {po_id}")
            return None
    
    def get_commitment_change_order_line_items(self, company_id: str, project_id: str, commitment_change_order_id: str) -> Optional[List[Dict]]:
        """Get line items for a specific commitment change order"""
        endpoint = f"/v2.0/companies/{company_id}/projects/{project_id}/commitment_change_orders/{commitment_change_order_id}/line_items"
        
        logger.info(f"🔍 Getting line items for commitment change order {commitment_change_order_id}")
        result = self._make_request('GET', endpoint)
        
        if result is not None:
            logger.info(f"📋 Found {len(result) if isinstance(result, list) else 'some'} line items for change order {commitment_change_order_id}")
            return result
        else:
            logger.error(f"❌ Failed to get line items for commitment change order {commitment_change_order_id}")
            return None
    
    def get_custom_field_definitions(self, project_id: str) -> Optional[List[Dict]]:
        """Get custom field definitions for commitments - boolean fields only"""
        endpoint = f"/v1.0/projects/{project_id}/configurable_field_sets"
        params = {
            'types[]': 'ConfigurableFieldSet::PurchaseOrderContract'
            # Note: No longer includes dropdown list-of-values since fields are now boolean checkboxes
        }
        
        logger.info(f"🔍 Getting custom field definitions from: {endpoint}")
        result = self._make_request('GET', endpoint, params=params)
        
        if result is not None:
            logger.info(f"✅ Successfully retrieved {len(result) if isinstance(result, list) else 'some'} field sets")
        else:
            logger.error(f"❌ Failed to retrieve custom field definitions")
            
        return result
    
    def get_custom_field_responses(self, project_id: str, commitment_id: str) -> Optional[List[Dict]]:
        """Get custom field responses for a commitment"""
        endpoint = f"/v1.0/projects/{project_id}/commitments/{commitment_id}/custom_field_responses"
        return self._make_request('GET', endpoint)
    
    def verify_custom_field_update(self, project_id: str, commitment_id: str, field_id: str) -> Optional[str]:
        """Verify custom field update by reading back the value"""
        endpoint = f"/v1.0/purchase_order_contracts/{commitment_id}"
        params = {'project_id': project_id}
        
        logger.info(f"🔍 Verifying custom field update for PO {commitment_id}")
        result = self._make_request('GET', endpoint, params=params)
        
        if result is not None:
            # Look for custom field values in the response
            custom_field_key = f"custom_field_{field_id}"
            
            # Check different possible locations for custom field values
            locations_to_check = [
                result,  # Direct in result
                result.get('custom_fields', {}),  # In custom_fields object
                result.get('custom_field_values', {}),  # In custom_field_values object
            ]
            
            for i, location in enumerate(locations_to_check):
                if isinstance(location, dict):
                    if i == 1:  # custom_fields location
                        logger.info(f"🔍 custom_fields content: {location}")
                    if custom_field_key in location:
                        current_value = location[custom_field_key]
                        logger.info(f"✅ Verified: {custom_field_key} = '{current_value}' (location {i})")
                        return current_value
            
            # Also check for the field name in the response
            logger.info(f"🔍 PO response keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")
            logger.warning(f"⚠️ Custom field {custom_field_key} not found in response")
            return None
        else:
            logger.error(f"❌ Failed to retrieve PO {commitment_id} for verification")
            return None
    
    def update_commitment_custom_field(self, project_id: str, commitment_id: str, field_updates: Dict, company_id: str = None) -> bool:
        """Update custom fields on a commitment (PO) using correct Procore API structure for boolean fields"""
        # Use the correct Procore API endpoint for updating purchase order contracts
        endpoint = f"/v1.0/purchase_order_contracts/{commitment_id}"
        
        # Convert field updates to the correct Procore format: custom_field_{id}
        custom_fields = {}
        for field_id, field_value in field_updates.items():
            # Use the exact format from Procore documentation
            custom_field_key = f"custom_field_{field_id}"
            
            # Convert boolean values to strings as required by Procore API
            if isinstance(field_value, bool):
                custom_fields[custom_field_key] = 'true' if field_value else 'false'
            else:
                custom_fields[custom_field_key] = field_value
        
        # Structure the payload according to Procore API documentation
        payload = {
            "project_id": int(project_id),
            "purchase_order_contract": custom_fields
        }
        
        logger.info(f"🔧 Updating PO {commitment_id} with custom fields: {custom_fields}")
        logger.info(f"🔍 Using endpoint: {endpoint}")
        logger.info(f"📦 Complete payload: {payload}")
        
        result = self._make_request('PATCH', endpoint, json=payload)
        
        if result is not None:
            logger.info(f"✅ Successfully updated custom fields on PO {commitment_id}")
            return True
        else:
            logger.error(f"❌ Failed to update custom fields on PO {commitment_id}")
            return False
    
    def get_approval_tier_field_ids(self, project_id: str) -> Dict[str, Optional[str]]:
        """Get the custom field definition IDs for all approval tier fields (Tier 1-5 checkboxes)"""
        field_sets = self.get_custom_field_definitions(project_id)
        if not field_sets:
            logger.error("❌ No configurable field sets found")
            return {}
        
        logger.info(f"🔍 Found {len(field_sets)} configurable field sets")
        
        # Initialize results dictionary
        tier_fields = {
            'Tier 1': None,
            'Tier 2': None, 
            'Tier 3': None,
            'Tier 4': None,
            'Tier 5': None
        }
        
        # Look through configurable field sets for tier fields
        for i, field_set in enumerate(field_sets):
            logger.info(f"📋 Field set {i}: {field_set.get('name', 'Unnamed')} (type: {field_set.get('type', 'Unknown')})")
            
            # Check fields object
            fields_obj = field_set.get('fields', {})
            logger.info(f"🔍 Found {len(fields_obj)} fields in this set")
            
            for field_key, field_data in fields_obj.items():
                if isinstance(field_data, dict):
                    field_name = field_data.get('name', '')
                    field_label = field_data.get('label', '')
                    field_id = field_data.get('id') or field_data.get('custom_field_definition_id')
                    field_type = field_data.get('field_type', '')
                    
                    logger.info(f"📝 Field '{field_key}': name='{field_name}', label='{field_label}', type='{field_type}' (ID: {field_id})")
                    
                    # Check if this is one of our tier fields (checkbox/boolean type)
                    for tier_name in tier_fields.keys():
                        if (field_name == tier_name or field_label == tier_name) and field_type in ['checkbox', 'boolean']:
                            logger.info(f"✅ Found {tier_name} field: '{field_name}' / '{field_label}' (ID: {field_id}, Type: {field_type})")
                            tier_fields[tier_name] = field_id
        
        # Log results
        found_fields = {k: v for k, v in tier_fields.items() if v is not None}
        missing_fields = [k for k, v in tier_fields.items() if v is None]
        
        if found_fields:
            logger.info(f"✅ Found tier fields: {found_fields}")
        if missing_fields:
            logger.warning(f"⚠️ Missing tier fields: {missing_fields}")
        
        return tier_fields
    
    def post_approval_decision(self, project_id: str, commitment_id: str, approval_tier: int, reason: str, company_id: str = None) -> bool:
        """Post approval decision back to Procore by updating tier checkbox custom fields"""
        try:
            # Get all the tier field IDs
            tier_field_ids = self.get_approval_tier_field_ids(project_id)
            if not any(tier_field_ids.values()):
                logger.error("❌ Cannot update approval tier: No tier custom fields found")
                logger.info("💡 Please create 'Tier 1', 'Tier 2', 'Tier 3', 'Tier 4', 'Tier 5' checkbox custom fields in Procore for commitments")
                return True
            
            # Map approval tier numbers to tier names
            tier_mapping = {
                ApprovalTier.TIER_1: "Tier 1",
                ApprovalTier.TIER_2: "Tier 2", 
                ApprovalTier.TIER_3: "Tier 3",
                ApprovalTier.TIER_4: "Tier 4",
                ApprovalTier.TIER_5: "Tier 5"
            }
            
            target_tier_name = tier_mapping.get(approval_tier, "Tier 5")
            
            # Prepare custom field updates - set only the target tier to true, others to false
            field_updates = {}
            for tier_name, field_id in tier_field_ids.items():
                if field_id:  # Only update fields we found
                    field_updates[field_id] = (tier_name == target_tier_name)
            
            if not field_updates:
                logger.error("❌ No valid tier fields found to update")
                return False
            
            logger.info(f"🎯 Setting {target_tier_name} = True, others = False")
            logger.info(f"🔧 Field updates: {field_updates}")
            
            # Update the commitment with the tier checkboxes
            success = self.update_commitment_custom_field(project_id, commitment_id, field_updates, company_id)
            
            if success:
                logger.info(f"✅ Updated approval tiers - {target_tier_name} checked")
                logger.info(f"📝 Reason: {reason}")
                
                # Verify the update by reading back the values
                for tier_name, field_id in tier_field_ids.items():
                    if field_id and tier_name == target_tier_name:
                        verified_value = self.verify_custom_field_update(project_id, commitment_id, field_id)
                        if verified_value:
                            logger.info(f"🔍 Verification: {tier_name} field contains '{verified_value}'")
                        else:
                            logger.warning(f"⚠️ Could not verify {tier_name} update")
            else:
                logger.error(f"❌ Failed to update tier custom fields")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Error posting approval decision: {e}")
            return False

class ApprovalEngine:
    """Business logic engine for determining approval tiers"""
    
    def __init__(self, api_client: ProcoreAPIClient):
        self.api = api_client
    
    def calculate_approval_tier_for_po(self, project_id: str, po_id: str, company_id: str) -> Tuple[int, str]:
        """Calculate the required approval tier for a specific PO"""
        try:
            logger.info(f"🧠 Processing approval logic for PO {po_id} in project {project_id}")
            
            # Step 1: Get PO details and amount
            po_amount = self._get_po_amount(project_id, po_id, company_id)
            if po_amount is None:
                return ApprovalTier.TIER_5, "Could not retrieve PO amount"
            
            # Step 2: Calculate revised budget
            revised_budget = self._calculate_revised_budget(company_id, project_id)
            if revised_budget is None:
                return ApprovalTier.TIER_5, "Could not calculate revised budget"
            
            # Step 4: Check if over budget first (highest priority)
            if po_amount > revised_budget:
                return ApprovalTier.TIER_5, f"PO amount ${po_amount:,.2f} exceeds revised budget ${revised_budget:,.2f}"
            
            # Step 5: Check for unapproved change orders attached to this PO
            if self._has_unapproved_change_orders(project_id, po_id):
                return ApprovalTier.TIER_4, f"Unapproved change orders attached to PO ${po_amount:,.2f} (under budget)"
            
            # Step 6: Check Ad-Hoc custom field (cost code 99-999) - forces Tier 3
            if self._is_ad_hoc_po(project_id, po_id, company_id):
                return ApprovalTier.TIER_3, f"Ad-Hoc PO (99-999 cost code) ${po_amount:,.2f} (under budget)"
            
            # Step 7: Base tier on PO amount (all under budget, no COs, not Ad-Hoc)
            approval_tier = self._get_base_approval_tier(po_amount)
            tier_reason = f"Amount-based tier {approval_tier} for PO ${po_amount:,.2f} (under budget)"
            
            logger.info(f"✅ Approval tier {approval_tier}: {tier_reason}")
            return approval_tier, tier_reason
            
        except Exception as e:
            logger.error(f"❌ Error calculating approval tier: {e}")
            return ApprovalTier.TIER_5, f"Error in approval calculation: {e}"
    
    def _get_po_amount(self, project_id: str, po_id: str, company_id: str = None) -> Optional[float]:
        """Get total PO amount from line items"""
        line_items = self.api.get_po_line_items(project_id, po_id, company_id)
        if not line_items:
            return None
        
        total_amount = sum(
            float(item.get('amount', 0) or 0) 
            for item in line_items
        )
        
        logger.info(f"📊 PO {po_id} total amount: ${total_amount:,.2f}")
        return total_amount
    
    def _calculate_revised_budget(self, company_id: str, project_id: str) -> Optional[float]:
        """Calculate revised budget = original + approved changes + approved COs by WBS alignment"""
        try:
            logger.info("💰 Calculating revised budget with WBS alignment...")
            
            # Get approved budget changes
            budget_changes = self.api.get_budget_changes(company_id, project_id)
            budget_change_amount = 0
            budget_wbs_amounts = {}  # Track amounts by WBS ID
            
            if budget_changes and isinstance(budget_changes, list):
                for change in budget_changes:
                    if isinstance(change, dict):
                        amount = float(change.get('amount', 0) or 0)
                        wbs_id = change.get('wbs_id') or change.get('wbs_code_id')
                        
                        if wbs_id:
                            budget_wbs_amounts[wbs_id] = budget_wbs_amounts.get(wbs_id, 0) + amount
                        
                        budget_change_amount += amount
                        logger.info(f"📊 Budget change: ${amount:,.2f} (WBS: {wbs_id})")
            
            # Get approved change orders and their line items
            approved_cos = self.api.get_change_orders(project_id, status='approved')
            co_amount = 0
            co_wbs_amounts = {}  # Track amounts by WBS ID
            
            if approved_cos and isinstance(approved_cos, list):
                for co in approved_cos:
                    if isinstance(co, dict):
                        co_id = co.get('id')
                        
                        # Get line items for this change order to align by WBS
                        co_line_items = self.api.get_commitment_change_order_line_items(company_id, project_id, co_id)
                        
                        if co_line_items and isinstance(co_line_items, list):
                            for line_item in co_line_items:
                                if isinstance(line_item, dict):
                                    amount = float(line_item.get('amount', 0) or 0)
                                    wbs_id = line_item.get('wbs_id') or line_item.get('wbs_code_id')
                                    
                                    if wbs_id:
                                        co_wbs_amounts[wbs_id] = co_wbs_amounts.get(wbs_id, 0) + amount
                                    
                                    co_amount += amount
                                    logger.info(f"📊 Change order line item: ${amount:,.2f} (WBS: {wbs_id}, CO: {co_id})")
                        else:
                            # Fallback to CO total amount if no line items
                            amount = float(co.get('amount', 0) or 0)
                            co_amount += amount
                            logger.info(f"📊 Change order total: ${amount:,.2f} (CO: {co_id}, no line items)")
            
            # Calculate WBS-aligned totals
            all_wbs_ids = set(budget_wbs_amounts.keys()) | set(co_wbs_amounts.keys())
            wbs_aligned_total = 0
            
            for wbs_id in all_wbs_ids:
                budget_amt = budget_wbs_amounts.get(wbs_id, 0)
                co_amt = co_wbs_amounts.get(wbs_id, 0)
                wbs_total = budget_amt + co_amt
                wbs_aligned_total += wbs_total
                
                if wbs_total != 0:
                    logger.info(f"📋 WBS {wbs_id}: Budget changes ${budget_amt:,.2f} + CO changes ${co_amt:,.2f} = ${wbs_total:,.2f}")
            
            # For original budget, you might need to get project budget from appropriate endpoint
            # This is a simplified calculation - you should replace with actual budget API call
            original_budget = 1000000  # TODO: Get this from Procore budget API
            
            revised_budget = original_budget + wbs_aligned_total
            
            logger.info(f"💰 Revised budget calculation:")
            logger.info(f"   Original budget: ${original_budget:,.2f}")
            logger.info(f"   WBS-aligned changes: ${wbs_aligned_total:,.2f}")
            logger.info(f"   Total budget changes: ${budget_change_amount:,.2f}")
            logger.info(f"   Total CO changes: ${co_amount:,.2f}")
            logger.info(f"   Final revised budget: ${revised_budget:,.2f}")
            
            return revised_budget
            
        except Exception as e:
            logger.error(f"❌ Error calculating revised budget: {e}")
            return None
    
    def _get_base_approval_tier(self, amount: float) -> int:
        """Get base approval tier based on PO amount"""
        if amount < 5000:
            return ApprovalTier.TIER_1
        elif amount <= 10000:
            return ApprovalTier.TIER_2
        else:
            return ApprovalTier.TIER_3
    
    def _has_unapproved_change_orders(self, project_id: str, po_id: str) -> bool:
        """Check if PO has unapproved change orders attached"""
        po_change_orders = self.api.get_po_change_orders(po_id)
        
        if not po_change_orders:
            logger.info(f"ℹ️ No change orders found for PO {po_id}")
            return False
        
        unapproved_count = 0
        for co in po_change_orders:
            status = co.get('status', 'unknown')
            if status != 'approved':
                unapproved_count += 1
                logger.info(f"⚠️ Found unapproved change order {co.get('id')} with status: {status}")
        
        has_unapproved = unapproved_count > 0
        
        if has_unapproved:
            logger.info(f"⚠️ Found {unapproved_count} unapproved change orders attached to PO {po_id}")
        else:
            logger.info(f"✅ All change orders for PO {po_id} are approved")
        
        return has_unapproved
    
    def _is_ad_hoc_po(self, project_id: str, po_id: str, company_id: str = None) -> bool:
        """Check if PO is Ad-Hoc by examining cost codes on line items"""
        try:
            logger.info(f"🔍 Checking for Ad-Hoc cost codes on PO {po_id}")
            
            # Get PO line items to check cost codes
            line_items = self.api.get_po_line_items(project_id, po_id, company_id)
            if not line_items:
                logger.warning(f"⚠️ No line items found for PO {po_id}")
                return False
            
            # Check each line item for cost code 99-999 (Unallocated Costs)
            for item in line_items:
                cost_code = item.get('cost_code', {})
                cost_code_name = cost_code.get('name', '') if isinstance(cost_code, dict) else str(cost_code)
                cost_code_code = cost_code.get('code', '') if isinstance(cost_code, dict) else ''
                
                # Check if cost code is 99-999 or contains "Unallocated"
                if (cost_code_code == '99-999' or 
                    'unallocated' in cost_code_name.lower() or
                    cost_code_name == '99-999'):
                    
                    logger.info(f"🔖 Ad-Hoc PO detected: Line item has cost code {cost_code_code} ({cost_code_name})")
                    return True
            
            logger.info(f"✅ PO {po_id} is not Ad-Hoc (no 99-999 cost codes found)")
            return False
            
        except Exception as e:
            logger.error(f"❌ Error checking Ad-Hoc cost codes: {e}")
            return False

# Global instances
api_client = ProcoreAPIClient()
approval_engine = ApprovalEngine(api_client)

def parse_webhook_payload(data: Dict) -> Optional[ProcoreWebhookPayload]:
    """Parse incoming webhook payload"""
    try:
        return ProcoreWebhookPayload(
            id=data.get('id'),
            timestamp=data.get('timestamp'),
            reason=data.get('reason'),
            company_id=data.get('company_id'),
            project_id=data.get('project_id'),
            user_id=data.get('user_id'),
            resource_type=data.get('resource_type'),
            resource_id=data.get('resource_id'),
            payload_version=data.get('payload_version'),
            data=data.get('data')
        )
    except Exception as e:
        logger.error(f"❌ Error parsing webhook payload: {e}")
        return None

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    logger.info("✅ Health check hit")
    return '✅ Procore Integration Service is running', 200

@app.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """OAuth callback endpoint for authorization code flow"""
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        logger.error(f"❌ OAuth error: {error}")
        return f'❌ OAuth error: {error}', 400
    
    if not code:
        return '❌ Missing authorization code', 400
    
    if api_client.authenticate(code):
        logger.info("✅ OAuth authentication successful")
        
        # LOG THE TOKEN DETAILS
        logger.info("🔑 ========== TOKEN DETAILS ==========")
        logger.info(f"🔑 ACCESS TOKEN: {api_client.access_token}")
        logger.info(f"🔄 REFRESH TOKEN: {api_client.refresh_token}")
        logger.info(f"⏰ EXPIRES AT: {api_client.token_expires_at}")
        logger.info(f"🌍 ENVIRONMENT: {api_client.environment}")
        logger.info("🔑 =====================================")
        
        # Also print to console for easy copying
        print("\n" + "="*60)
        print("🎉 PROCORE ACCESS TOKEN GENERATED")
        print("="*60)
        print(f"Access Token: {api_client.access_token}")
        print(f"Refresh Token: {api_client.refresh_token}")
        print(f"Expires At: {api_client.token_expires_at}")
        print(f"Environment: {api_client.environment}")
        print("\n📋 Environment Variables:")
        print(f"export PROCORE_ACCESS_TOKEN='{api_client.access_token}'")
        if api_client.refresh_token:
            print(f"export PROCORE_REFRESH_TOKEN='{api_client.refresh_token}'")
        if api_client.token_expires_at:
            print(f"export PROCORE_TOKEN_EXPIRES_AT='{api_client.token_expires_at.isoformat()}'")
        print("="*60)
        
        return '''
        ✅ Authentication successful! 
        
        Check the application logs for your access token details.
        
        You can also copy the environment variables from the console output.
        ''', 200
    else:
        return '❌ Authentication failed', 500

@app.route('/auth/status', methods=['GET'])
def auth_status():
    """Check current authentication status"""
    try:
        if api_client.access_token and api_client._ensure_valid_token():
            return jsonify({
                'status': 'authenticated',
                'expires_at': api_client.token_expires_at.isoformat() if api_client.token_expires_at else None,
                'has_refresh_token': bool(api_client.refresh_token),
                'environment': api_client.environment
            }), 200
        else:
            return jsonify({
                'status': 'not_authenticated',
                'message': 'Need to complete OAuth flow',
                'oauth_url': f"https://{api_client.oauth_base.split('//')[1]}/oauth/authorize?client_id={PROCORE_CLIENT_ID}&response_type=code&redirect_uri={PROCORE_REDIRECT_URI}"
            }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Handle Procore webhook and process approval logic"""
    try:
        logger.info("✅ Webhook hit")
        
        # Log request details
        headers = dict(request.headers)
        logger.info(f"📩 Headers: {headers}")
        
        raw_body = request.data.decode('utf-8', errors='replace')
        logger.info(f"📦 Raw Body: {raw_body}")
        
        # Parse JSON payload
        json_data = request.get_json(silent=True)
        if not json_data:
            logger.warning("⚠️ No JSON payload could be parsed")
            return '⚠️ Invalid JSON payload', 400
        
        logger.info(f"📄 Parsed JSON: {json_data}")
        
        # Parse webhook payload
        payload = parse_webhook_payload(json_data)
        if not payload:
            return '❌ Failed to parse webhook payload', 400
        
        # Process Purchase Order Contract events AND line item events
        if payload.resource_type not in ['Purchase Order Contracts', 'Purchase Order Contract Line Items']:
            logger.info(f"ℹ️ Ignoring {payload.resource_type} event")
            return '✅ Event ignored (not a PO event)', 200
        
        # Only process create/update events
        if payload.reason not in ['create', 'update']:
            logger.info(f"ℹ️ Ignoring {payload.reason} event")
            return '✅ Event ignored (not create/update)', 200
        
        # Extract the actual PO ID to process
        po_id_to_process = None
        
        if payload.resource_type == 'Purchase Order Contracts':
            # Direct PO event - use the resource_id
            po_id_to_process = payload.resource_id
            logger.info(f"📋 Processing direct PO event for PO {po_id_to_process}")
            
        elif payload.resource_type == 'Purchase Order Contract Line Items':
            # Line item event - extract related PO ID
            related_resources = payload.data.get('related_resources', []) if payload.data else []
            for resource in related_resources:
                if resource.get('name') == 'Purchase Order Contracts':
                    po_id_to_process = str(resource.get('id'))
                    logger.info(f"📋 Processing line item event for related PO {po_id_to_process}")
                    break
            
            if not po_id_to_process:
                logger.warning("⚠️ No related Purchase Order Contract found in line item webhook")
                return '⚠️ No related PO found', 400
        
        if not po_id_to_process:
            logger.error("❌ Could not determine PO ID to process")
            return '❌ Cannot determine PO ID', 400
        
        # Authenticate with Procore if not already authenticated
        if not api_client.access_token or not api_client._ensure_valid_token():
            logger.error("❌ Not authenticated with Procore - webhook cannot be processed")
            return '❌ Authentication required', 401
        
        # Calculate approval tier using the determined PO ID
        approval_tier, reason = approval_engine.calculate_approval_tier_for_po(
            payload.project_id, 
            po_id_to_process, 
            payload.company_id
        )
        
        # Post approval decision back to Procore by updating tier checkbox custom fields
        success = api_client.post_approval_decision(
            payload.project_id, 
            po_id_to_process, 
            approval_tier, 
            reason, 
            payload.company_id
        )
        
        if success:
            logger.info(f"✅ Approval decision posted for PO {po_id_to_process}: Tier {approval_tier}")
            return jsonify({
                'status': 'success',
                'processed_po_id': po_id_to_process,
                'webhook_resource_type': payload.resource_type,
                'webhook_resource_id': payload.resource_id,
                'approval_tier': approval_tier,
                'reason': reason
            }), 200
        else:
            logger.error(f"❌ Failed to post approval decision for PO {po_id_to_process}")
            return '❌ Failed to post approval decision', 500
        
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}")
        return f'❌ Error processing webhook: {e}', 500

if __name__ == '__main__':
    # Validate required environment variables
    required_vars = [
        'PROCORE_CLIENT_ID',
        'PROCORE_CLIENT_SECRET',
        'PROCORE_REDIRECT_URI'
    ]
    
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {missing_vars}")
        sys.exit(1)
    
    logger.info(f"🚀 Starting Procore Integration Service (Environment: {PROCORE_ENVIRONMENT})...")
    logger.info(f"🔗 OAuth callback URL: {PROCORE_REDIRECT_URI}")
    
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
