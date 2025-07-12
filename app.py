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
    AUTO_APPROVE = 1
    PROJECT_MANAGER = 2
    PROJECT_DIRECTOR = 3
    OPERATIONS_MANAGER = 4
    MANAGING_DIRECTOR = 5

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
    
    def get_po_line_items(self, project_id: str, po_id: str) -> Optional[List[Dict]]:
        """Get PO line items - corrected endpoint"""
        endpoint = f"/v1.0/projects/{project_id}/purchase_order_contracts/{po_id}/purchase_order_contract_line_items"
        return self._make_request('GET', endpoint)
    
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
    
    def get_custom_field_definitions(self, project_id: str) -> Optional[List[Dict]]:
        """Get custom field definitions for commitments - corrected endpoint"""
        endpoint = f"/v1.0/projects/{project_id}/configurable_field_sets"
        params = {'origin_id': 'commitments'}
        return self._make_request('GET', endpoint, params=params)
    
    def get_custom_field_responses(self, project_id: str, commitment_id: str) -> Optional[List[Dict]]:
        """Get custom field responses for a commitment"""
        endpoint = f"/v1.0/projects/{project_id}/commitments/{commitment_id}/custom_field_responses"
        return self._make_request('GET', endpoint)
    
    def update_commitment_custom_field(self, project_id: str, commitment_id: str, field_updates: Dict) -> bool:
        """Update custom fields on a commitment (PO)"""
        endpoint = f"/v1.0/projects/{project_id}/commitments/{commitment_id}"
        
        # Structure the payload for updating custom fields
        payload = {
            'custom_field_values': field_updates
        }
        
        result = self._make_request('PATCH', endpoint, json=payload)
        return result is not None
    
    def get_approval_tier_field_id(self, project_id: str) -> Optional[str]:
        """Get the custom field definition ID for 'Approval Tier' field"""
        field_sets = self.get_custom_field_definitions(project_id)
        if not field_sets:
            return None
        
        # Look through configurable field sets for approval tier field
        for field_set in field_sets:
            custom_fields = field_set.get('custom_fields', [])
            for field in custom_fields:
                field_name = field.get('name', '').lower()
                if 'approval tier' in field_name or 'approval_tier' in field_name:
                    return field.get('id')
        
        logger.warning("⚠️ 'Approval Tier' custom field not found in any fieldset")
        return None
    
    def post_approval_decision(self, project_id: str, commitment_id: str, approval_tier: int, reason: str) -> bool:
        """Post approval decision back to Procore by updating custom field"""
        try:
            # Get the Approval Tier custom field ID
            approval_field_id = self.get_approval_tier_field_id(project_id)
            if not approval_field_id:
                logger.error("❌ Cannot update approval tier: 'Approval Tier' custom field not found")
                logger.info("💡 Please create an 'Approval Tier' custom field in Procore for commitments")
                # Still return success so we can see the approval tier calculation in logs
                return True
            
            # Map approval tier numbers to human-readable values
            tier_mapping = {
                ApprovalTier.AUTO_APPROVE: "Auto-Approval",
                ApprovalTier.PROJECT_MANAGER: "Project Manager", 
                ApprovalTier.PROJECT_DIRECTOR: "Project Director",
                ApprovalTier.OPERATIONS_MANAGER: "Operations Manager",
                ApprovalTier.MANAGING_DIRECTOR: "Managing Director"
            }
            
            approval_value = tier_mapping.get(approval_tier, "Managing Director")
            
            # Prepare custom field update
            field_updates = {
                approval_field_id: approval_value
            }
            
            # Update the commitment with the approval tier
            success = self.update_commitment_custom_field(project_id, commitment_id, field_updates)
            
            if success:
                logger.info(f"✅ Updated Approval Tier to: {approval_value}")
                logger.info(f"📝 Reason: {reason}")
            else:
                logger.error(f"❌ Failed to update Approval Tier custom field")
            
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
            po_amount = self._get_po_amount(project_id, po_id)
            if po_amount is None:
                return ApprovalTier.MANAGING_DIRECTOR, "Could not retrieve PO amount"
            
            # Step 2: Calculate revised budget
            revised_budget = self._calculate_revised_budget(company_id, project_id)
            if revised_budget is None:
                return ApprovalTier.MANAGING_DIRECTOR, "Could not calculate revised budget"
            
            # Step 3: Check if over budget
            if po_amount > revised_budget:
                return ApprovalTier.MANAGING_DIRECTOR, f"PO amount ${po_amount:,.2f} exceeds revised budget ${revised_budget:,.2f}"
            
            # Step 4: Base tier on PO amount
            approval_tier = self._get_base_approval_tier(po_amount)
            tier_reason = f"Base tier {approval_tier} for PO amount ${po_amount:,.2f}"
            
            # Step 5: Check for unapproved change orders
            if self._has_unapproved_change_orders(project_id):
                approval_tier = max(approval_tier, ApprovalTier.OPERATIONS_MANAGER)
                tier_reason += " + unapproved COs present"
            
            # Step 6: Check Ad-Hoc custom field (cost code 99-999)
            if self._is_ad_hoc_po(project_id, po_id):
                approval_tier = max(approval_tier, ApprovalTier.PROJECT_DIRECTOR)
                tier_reason += " + Ad-Hoc PO (99-999 cost code)"
            
            logger.info(f"✅ Approval tier {approval_tier}: {tier_reason}")
            return approval_tier, tier_reason
            
        except Exception as e:
            logger.error(f"❌ Error calculating approval tier: {e}")
            return ApprovalTier.MANAGING_DIRECTOR, f"Error in approval calculation: {e}"
    
    def _get_po_amount(self, project_id: str, po_id: str) -> Optional[float]:
        """Get total PO amount from line items"""
        line_items = self.api.get_po_line_items(project_id, po_id)
        if not line_items:
            return None
        
        total_amount = sum(
            float(item.get('amount', 0) or 0) 
            for item in line_items
        )
        
        logger.info(f"📊 PO {po_id} total amount: ${total_amount:,.2f}")
        return total_amount
    
    def _calculate_revised_budget(self, company_id: str, project_id: str) -> Optional[float]:
        """Calculate revised budget = original + approved changes + approved COs"""
        try:
            # Get approved budget changes
            budget_changes = self.api.get_budget_changes(company_id, project_id)
            budget_change_amount = sum(
                float(change.get('amount', 0) or 0)
                for change in (budget_changes or [])
            )
            
            # Get approved change orders
            approved_cos = self.api.get_change_orders(project_id, status='approved')
            co_amount = sum(
                float(co.get('amount', 0) or 0)
                for co in (approved_cos or [])
            )
            
            # For original budget, you might need to get project budget
            # This is a simplified calculation - adjust based on your needs
            original_budget = 1000000  # You'll need to get this from appropriate endpoint
            
            revised_budget = original_budget + budget_change_amount + co_amount
            
            logger.info(f"💰 Revised budget: ${original_budget:,.2f} + ${budget_change_amount:,.2f} + ${co_amount:,.2f} = ${revised_budget:,.2f}")
            return revised_budget
            
        except Exception as e:
            logger.error(f"❌ Error calculating revised budget: {e}")
            return None
    
    def _get_base_approval_tier(self, amount: float) -> int:
        """Get base approval tier based on PO amount"""
        if amount <= 500000:
            return ApprovalTier.AUTO_APPROVE
        elif amount <= 1000000:
            return ApprovalTier.PROJECT_MANAGER
        else:
            return ApprovalTier.PROJECT_DIRECTOR
    
    def _has_unapproved_change_orders(self, project_id: str) -> bool:
        """Check if project has unapproved change orders"""
        unapproved_cos = self.api.get_change_orders(project_id, status='pending')
        has_unapproved = bool(unapproved_cos and len(unapproved_cos) > 0)
        
        if has_unapproved:
            logger.info(f"⚠️ Found {len(unapproved_cos)} unapproved change orders")
        
        return has_unapproved
    
    def _is_ad_hoc_po(self, project_id: str, po_id: str) -> bool:
        """Check if PO is Ad-Hoc by examining cost codes on line items"""
        try:
            logger.info(f"🔍 Checking for Ad-Hoc cost codes on PO {po_id}")
            
            # Get PO line items to check cost codes
            line_items = self.api.get_po_line_items(project_id, po_id)
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
        return '✅ Authentication successful! Webhook processing is now active.', 200
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
        
        # Post approval decision back to Procore by updating custom field
        success = api_client.post_approval_decision(
            payload.project_id,
            po_id_to_process,  # Use the main PO ID, not line item ID
            approval_tier,
            reason
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
