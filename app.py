import os
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests
from flask import Flask, request, jsonify
from dataclasses import dataclass
import json
from collections import defaultdict

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
PROCORE_ENVIRONMENT = os.environ.get('PROCORE_ENVIRONMENT', 'sandbox')

# Custom Field IDs for Tier checkboxes
TIER_FIELD_IDS = {
    'Tier 1': '4334',
    'Tier 2': '4335',
    'Tier 3': '4336',
    'Tier 4': '4337',
    'Tier 5': '4338'
}

@dataclass
class ApprovalTier:
    """Approval tier definitions"""
    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4
    TIER_5 = 5

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
    """Procore API client with OAuth 2.0 authentication and environment variable support"""
    
    def __init__(self):
        self.access_token = os.environ.get('PROCORE_ACCESS_TOKEN')
        self.refresh_token = os.environ.get('PROCORE_REFRESH_TOKEN')
        self.token_expires_at = None
        self.environment = PROCORE_ENVIRONMENT
        
        # Set base URLs based on environment
        if self.environment == 'production':
            self.oauth_base = 'https://login.procore.com'
            self.api_base = 'https://api.procore.com'
        else:  # sandbox
            self.oauth_base = 'https://sandbox.procore.com'
            self.api_base = 'https://sandbox.procore.com'
        
        # If we have a token from environment, log it
        if self.access_token:
            logger.info(f"Loaded access token from environment variable (length: {len(self.access_token)})")
    
    def authenticate(self, authorization_code: str = None):
        """Authenticate using OAuth 2.0"""
        if authorization_code:
            return self._get_access_token_from_code(authorization_code)
        else:
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
            logger.info(f"Exchanging authorization code for access token ({self.environment})")
            response = requests.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            self.refresh_token = token_data.get('refresh_token')
            expires_in = token_data.get('expires_in', 7200)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(f"NEW_ACCESS_TOKEN: {self.access_token}")
            logger.info(f"NEW_REFRESH_TOKEN: {self.refresh_token}")
            logger.info(f"Successfully authenticated with Procore {self.environment}")
            logger.info(f"DEPLOY_COMMAND: Add these to your next deployment:")
            logger.info(f"--set-env-vars='PROCORE_ACCESS_TOKEN={self.access_token},PROCORE_REFRESH_TOKEN={self.refresh_token}'")
            
            return True
            
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False
    
    def _refresh_access_token(self) -> bool:
        """Refresh the access token"""
        if not self.refresh_token:
            logger.error("No refresh token available")
            return False
            
        url = f"{self.oauth_base}/oauth/token"
        data = {
            'grant_type': 'refresh_token',
            'client_id': PROCORE_CLIENT_ID,
            'client_secret': PROCORE_CLIENT_SECRET,
            'refresh_token': self.refresh_token
        }
        
        try:
            response = requests.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
            
            self.access_token = token_data['access_token']
            self.refresh_token = token_data.get('refresh_token', self.refresh_token)
            expires_in = token_data.get('expires_in', 7200)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info(f"REFRESHED_ACCESS_TOKEN: {self.access_token}")
            logger.info(f"REFRESHED_REFRESH_TOKEN: {self.refresh_token}")
            logger.info("Successfully refreshed access token")
            
            return True
            
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return False
    
    def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid access token"""
        if not self.access_token:
            return False
            
        if self.token_expires_at and datetime.now() >= self.token_expires_at:
            return self._refresh_access_token()
            
        return True
    
    def get_purchase_order_by_id(self, resource_id: str, project_id: str, company_id: str) -> Optional[Dict]:
        """Get specific PO using filters"""
        if not self._ensure_valid_token():
            logger.error("Cannot make request: no valid token")
            return None
            
        url = f"{self.api_base}/rest/v1.0/purchase_order_contracts"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {
            'project_id': project_id,
            'filters[id]': resource_id
        }
        
        try:
            logger.info(f"Getting PO {resource_id} for project {project_id}")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            po_data = response.json()
            
            if not po_data or len(po_data) == 0:
                logger.error(f"PO {resource_id} not found")
                return None
                
            return po_data[0]  # Return first (should be only) result
            
        except Exception as e:
            logger.error(f"Failed to get PO {resource_id}: {e}")
            return None
    
    def get_po_line_items(self, resource_id: str, project_id: str, company_id: str) -> Optional[List[Dict]]:
        """Get PO line items"""
        if not self._ensure_valid_token():
            logger.error("Cannot make request: no valid token")
            return None
            
        url = f"{self.api_base}/rest/v1.0/purchase_order_contracts/{resource_id}/line_items"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'project_id': project_id}
        
        try:
            logger.info(f"Getting line items for PO {resource_id}")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Failed to get line items for PO {resource_id}: {e}")
            return None
    
    def get_budget_views(self, project_id: str, company_id: str) -> Optional[List[Dict]]:
        """Get budget views"""
        if not self._ensure_valid_token():
            logger.error("Cannot make request: no valid token")
            return None
            
        url = f"{self.api_base}/rest/v1.0/budget_views"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'project_id': project_id}
        
        try:
            logger.info(f"Getting budget views for project {project_id}")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Failed to get budget views: {e}")
            return None
    
    def get_budget_detail_rows(self, budget_view_id: str, project_id: str, company_id: str) -> Optional[List[Dict]]:
        """Get budget detail rows"""
        if not self._ensure_valid_token():
            logger.error("Cannot make request: no valid token")
            return None
            
        url = f"{self.api_base}/rest/v1.0/budget_views/{budget_view_id}/detail_rows"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'project_id': project_id}
        
        try:
            logger.info(f"Getting budget detail rows for view {budget_view_id}")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Failed to get budget detail rows: {e}")
            return None
    
    def update_po_tiers(self, resource_id: str, project_id: str, company_id: str, tier: int) -> bool:
        """Update PO with tier checkboxes"""
        if not self._ensure_valid_token():
            logger.error("Cannot make request: no valid token")
            return False
            
        url = f"{self.api_base}/rest/v1.0/purchase_order_contracts/{resource_id}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'run_configurable_validations': 'true'}
        
        # Set only the target tier to true, others to false
        custom_fields = {}
        for tier_name, field_id in TIER_FIELD_IDS.items():
            tier_num = int(tier_name.split()[1])
            custom_fields[f"custom_field_{field_id}"] = 'true' if tier_num == tier else 'false'
        
        payload = {
            "project_id": int(project_id),
            "purchase_order_contract": custom_fields
        }
        
        try:
            logger.info(f"Updating PO {resource_id} with Tier {tier}")
            response = requests.patch(url, headers=headers, params=params, json=payload)
            response.raise_for_status()
            
            logger.info(f"Updated PO {resource_id} to Tier {tier}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update PO {resource_id}: {e}")
            return False

class ApprovalEngine:
    """Business logic for approval tiers"""
    
    def __init__(self, api_client: ProcoreAPIClient):
        self.api = api_client
    
    def calculate_approval_tier(self, project_id: str, resource_id: str, company_id: str) -> Tuple[int, str]:
        """Calculate approval tier using updated business logic"""
        try:
            logger.info(f"Processing approval logic for PO {resource_id}")
            logger.info(f"Keys identified: company_id: {company_id}, project_id: {project_id}, resource_id: {resource_id}")
            
            # Step 2: Authentication Check (already done in _ensure_valid_token)
            logger.info("Successfully authenticated with Procore sandbox")
            
            # Step 3: Purchase Order Data Extraction
            logger.info("=== STEP 3: PO DATA EXTRACTION ===")
            po_data = self.api.get_purchase_order_by_id(resource_id, project_id, company_id)
            if not po_data:
                logger.error(f"Could not retrieve PO {resource_id}")
                return ApprovalTier.TIER_5, f"Could not retrieve PO {resource_id}"
            
            grand_total = float(po_data.get('grand_total', 0) or 0)
            has_potential_change_orders = bool(po_data.get('has_potential_change_orders', False))
            
            logger.info(f"{resource_id}_PO_data: Grand_total={grand_total}, has_potential_change_orders={has_potential_change_orders}")
            
            # Step 4: Line Items Analysis
            logger.info("=== STEP 4: LINE ITEMS ANALYSIS ===")
            line_items = self.api.get_po_line_items(resource_id, project_id, company_id)
            if not line_items:
                logger.error(f"Could not retrieve line items for PO {resource_id}")
                return ApprovalTier.TIER_5, f"Could not retrieve line items for PO {resource_id}"
            
            # Process line items and group by wbs_code
            line_items_by_wbs = defaultdict(list)
            unallocated_cost_present = False
            
            for item in line_items:
                line_item_id = item.get('id')
                po_line_item_id = f"{resource_id}_{line_item_id}"
                amount = float(item.get('amount', 0) or 0)
                wbs_code = item.get('wbs_code', {})
                cost_code = item.get('cost_code', {})
                
                # Check for unallocated costs
                cost_code_id = cost_code.get('id') if isinstance(cost_code, dict) else None
                if cost_code_id == 9427186:
                    unallocated_cost_present = True
                
                # Group by wbs_code for budget matching
                wbs_key = self._get_wbs_key(wbs_code)
                line_items_by_wbs[wbs_key].append({
                    'po_line_item_id': po_line_item_id,
                    'amount': amount,
                    'wbs_code': wbs_code,
                    'cost_code_id': cost_code_id
                })
                
                logger.info(f"Line item {po_line_item_id}: amount={amount}, wbs_code={wbs_code}, cost_code_id={cost_code_id}")
            
            logger.info(f"Unallocated_Cost_Present = {unallocated_cost_present}")
            
            # Step 5: Budget View Data Retrieval
            logger.info("=== STEP 5: BUDGET VIEW ID STORED ===")
            budget_views = self.api.get_budget_views(project_id, company_id)
            if not budget_views:
                logger.error("Could not get budget views")
                return ApprovalTier.TIER_5, "Could not get budget views"
            
            budget_view_id = budget_views[0]['id']
            logger.info(f"budget_view_id: {budget_view_id}")
            
            # Step 6: Budget View Detail Rows Retrieval
            logger.info("=== STEP 6: BUDGET DATA RECEIVED ===")
            budget_rows = self.api.get_budget_detail_rows(budget_view_id, project_id, company_id)
            if not budget_rows:
                logger.error("Could not get budget detail rows")
                return ApprovalTier.TIER_5, "Could not get budget detail rows"
            
            # Create budget lookup by wbs_code
            budget_by_wbs = {}
            for row in budget_rows:
                wbs_code = row.get('wbs_code', {})
                wbs_key = self._get_wbs_key(wbs_code)
                if wbs_key:
                    revised_budget = float(row.get('Revised Budget', 0) or 0)
                    committed_costs = float(row.get('Committed Costs', 0) or 0)
                    budget_by_wbs[wbs_key] = {
                        'revised_budget': revised_budget,
                        'committed_costs': committed_costs,
                        'wbs_code': wbs_code
                    }
                    
                    description = wbs_code.get('description', 'Unknown')
                    logger.info(f"wbs_code {description} found, Revised_Budget = {revised_budget}, Committed Costs = {committed_costs}")
            
            # Step 7: Over-Budget Analysis
            logger.info("=== STEP 7: Over-Budget Analysis ===")
            is_any_overbudget = False
            overbudget_details = []
            
            for wbs_key, line_items_list in line_items_by_wbs.items():
                # Sum all line items with same wbs_code
                total_po_amount = sum(item['amount'] for item in line_items_list)
                line_item_amounts = [item['amount'] for item in line_items_list]
                wbs_description = line_items_list[0]['wbs_code'].get('description', 'Unknown')
                
                logger.info(f"Summed line items for wbs_code {wbs_description}: amounts={line_item_amounts}, total={total_po_amount}")
                
                # Find matching budget row
                budget_data = budget_by_wbs.get(wbs_key)
                if not budget_data:
                    logger.error(f"No matching budget row found for wbs_code: {wbs_key}")
                    return ApprovalTier.TIER_5, f"No matching budget row found for wbs_code: {wbs_key}"
                
                # Calculate future committed costs
                revised_budget = budget_data['revised_budget']
                committed_costs = budget_data['committed_costs']
                future_committed_costs = committed_costs + total_po_amount
                is_overbudget = future_committed_costs > revised_budget
                
                if is_overbudget:
                    is_any_overbudget = True
                    overbudget_details.append(f"{wbs_description}: ${future_committed_costs} > ${revised_budget}")
                    logger.info(f"wbs_code {wbs_description} is over budget. CALCULATION: is_overbudget = true")
                else:
                    logger.info(f"wbs_code {wbs_description} isn't over budget. CALCULATION: is_overbudget = false")
            
            # Step 8: Business Logic - Tier Assignment
            logger.info("=== BUSINESS_LOGIC: APPROVAL_TIER_CALCULATION ===")
            
            # Tier 5 Conditions (Highest Priority)
            if is_any_overbudget:
                reason = f"Over budget on wbs_code level: {'; '.join(overbudget_details)}"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_5_CONDITION_1_TRIGGERED: is_over_budget=True")
                return ApprovalTier.TIER_5, reason
            
            if has_potential_change_orders and unallocated_cost_present:
                reason = "Has Potential Change Order and Unallocated Cost"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_5_CONDITION_2_TRIGGERED")
                return ApprovalTier.TIER_5, reason
            
            # Tier 4 Conditions
            if has_potential_change_orders:
                reason = "Has potential change order=true"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_4_CONDITION_1_TRIGGERED")
                return ApprovalTier.TIER_4, reason
            
            # Tier 3 Conditions
            if unallocated_cost_present:
                reason = "Unallocated cost code"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_3_CONDITION_1_TRIGGERED")
                return ApprovalTier.TIER_3, reason
            
            if grand_total > 10000:
                reason = "Grand total > $10,000"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_3_CONDITION_2_TRIGGERED")
                return ApprovalTier.TIER_3, reason
            
            # Tier 2 Conditions
            if 5000 < grand_total < 10000:
                reason = "Grand total between $5,000-$10,000"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_2_CONDITION_1_TRIGGERED")
                return ApprovalTier.TIER_2, reason
            
            # Tier 1 (Default)
            reason = "Grand total < $5,000"
            logger.info("BUSINESS_LOGIC_DECISION: TIER_1_DEFAULT_TRIGGERED")
            return ApprovalTier.TIER_1, reason
            
        except Exception as e:
            logger.error(f"Error calculating approval tier: {e}")
            return ApprovalTier.TIER_5, f"Error in calculation: {e}"
    
    def _get_wbs_key(self, wbs_code: Dict) -> Optional[str]:
        """Get wbs_code key for matching with priority: id -> flat_code -> description"""
        if not isinstance(wbs_code, dict):
            return None
            
        # Priority 1: id
        if wbs_code.get('id'):
            return f"id:{wbs_code['id']}"
        
        # Priority 2: flat_code
        if wbs_code.get('flat_code'):
            return f"flat_code:{wbs_code['flat_code']}"
        
        # Priority 3: description
        if wbs_code.get('description'):
            return f"description:{wbs_code['description']}"
        
        return None

# Global instances
api_client = ProcoreAPIClient()
approval_engine = ApprovalEngine(api_client)

def parse_webhook_payload(data: Dict) -> Optional[ProcoreWebhookPayload]:
    """Parse webhook payload"""
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
        logger.error(f"Error parsing webhook payload: {e}")
        return None

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    logger.info("Health check hit")
    return 'Procore Integration Service is running', 200

@app.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """OAuth callback endpoint"""
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        logger.error(f"OAuth error: {error}")
        return f'OAuth error: {error}', 400
    
    if not code:
        return 'Missing authorization code', 400
    
    if api_client.authenticate(code):
        logger.info("OAuth authentication successful")
        return 'Authentication successful! Webhook processing is now active.', 200
    else:
        return 'Authentication failed', 500

@app.route('/auth/status', methods=['GET'])
def auth_status():
    """Check authentication status"""
    try:
        if api_client.access_token and api_client._ensure_valid_token():
            token_preview = f"{api_client.access_token[:20]}...{api_client.access_token[-10:]}" if len(api_client.access_token) > 30 else api_client.access_token
            
            return jsonify({
                'status': 'authenticated',
                'expires_at': api_client.token_expires_at.isoformat() if api_client.token_expires_at else None,
                'environment': api_client.environment,
                'token_preview': token_preview,
                'token_length': len(api_client.access_token),
                'full_token': api_client.access_token,
                'refresh_token': api_client.refresh_token
            }), 200
        else:
            return jsonify({
                'status': 'not_authenticated',
                'oauth_url': f"https://{api_client.oauth_base.split('//')[1]}/oauth/authorize?client_id={PROCORE_CLIENT_ID}&response_type=code&redirect_uri={PROCORE_REDIRECT_URI}"
            }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Handle Procore webhook"""
    try:
        logger.info("Webhook received")
        
        # Quick validation - return fast for invalid requests
        if not request.is_json:
            logger.warning("Non-JSON webhook received")
            return 'OK', 200
        
        # Parse JSON payload
        json_data = request.get_json(silent=True)
        if not json_data:
            logger.warning("Invalid JSON payload")
            return 'OK', 200
        
        # Parse webhook payload
        payload = parse_webhook_payload(json_data)
        if not payload:
            logger.warning("Failed to parse webhook payload")
            return 'OK', 200
        
        # Only process PO events - return quickly for others
        if payload.resource_type not in ['Purchase Order Contracts', 'Purchase Order Contract Line Items']:
            logger.info(f"Ignoring {payload.resource_type} event")
            return 'OK', 200
        
        # Only process create/update events
        if payload.reason not in ['create', 'update']:
            logger.info(f"Ignoring {payload.reason} event")
            return 'OK', 200
        
        # Determine PO ID to process
        po_id = None
        if payload.resource_type == 'Purchase Order Contracts':
            po_id = payload.resource_id
        elif payload.resource_type == 'Purchase Order Contract Line Items':
            # Extract related PO ID from line item event
            related_resources = payload.data.get('related_resources', []) if payload.data else []
            for resource in related_resources:
                if resource.get('name') == 'Purchase Order Contracts':
                    po_id = str(resource.get('id'))
                    break
        
        if not po_id:
            logger.warning("Cannot determine PO ID")
            return 'OK', 200
        
        # Check authentication - return OK but log if not authenticated
        if not api_client.access_token or not api_client._ensure_valid_token():
            logger.error("Not authenticated - skipping processing")
            return 'OK', 200
        
        # Process approval tier calculation
        try:
            # Calculate approval tier
            approval_tier, reason = approval_engine.calculate_approval_tier(
                payload.project_id, 
                po_id, 
                payload.company_id
            )
            
            # Update PO with tier
            success = api_client.update_po_tiers(
                po_id,
                payload.project_id, 
                payload.company_id,
                approval_tier
            )
            
            if success:
                logger.info(f"Updated PO {po_id} to Tier {approval_tier}")
            else:
                logger.error(f"Failed to update PO {po_id}")
                
        except Exception as processing_error:
            logger.error(f"Error in processing: {processing_error}")
        
        # Always return OK to Procore
        return 'OK', 200
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        # Always return OK to avoid webhook queueing
        return 'OK', 200

if __name__ == '__main__':
    # Validate environment variables
    required_vars = ['PROCORE_CLIENT_ID', 'PROCORE_CLIENT_SECRET', 'PROCORE_REDIRECT_URI']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)
    
    logger.info(f"Starting Procore Integration Service (Environment: {PROCORE_ENVIRONMENT})")
    logger.info(f"OAuth callback URL: {PROCORE_REDIRECT_URI}")
    
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
