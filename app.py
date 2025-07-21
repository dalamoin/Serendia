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

# Custom Field IDs for Tier checkboxes (update these with your actual field IDs)
TIER_FIELD_IDS = {
    'Auto-Approve': '4334',
    'Tier 1': '4335',
    'Tier 2': '4336',
    'Tier 3': '4337',
    'Tier 4': '4338'
}

@dataclass
class ApprovalTier:
    """Approval tier definitions"""
    AUTO_APPROVE = "Auto-Approve"
    TIER_1 = "Tier 1"
    TIER_2 = "Tier 2"
    TIER_3 = "Tier 3"
    TIER_4 = "Tier 4"

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

class UIUpdatingProcoreAPIClient:
    """Procore API client with UI update capabilities"""
    
    def __init__(self):
        self.access_token = os.environ.get('PROCORE_ACCESS_TOKEN')
        self.refresh_token = os.environ.get('PROCORE_REFRESH_TOKEN')
        self.token_expires_at = None
        self.environment = PROCORE_ENVIRONMENT
        
        # Processing statistics
        self.webhooks_processed = 0
        self.pos_processed = 0
        self.approval_updates = 0
        self.ui_updates = 0
        self.processing_errors = 0
        
        # Monitoring settings
        self.refresh_count = 0
        self.token_created_at = datetime.now()
        self.consecutive_failures = 0
        self.last_refresh_success = None
        self.last_refresh_failure = None
        
        # Health monitoring thresholds
        self.refresh_alert_threshold = int(os.environ.get('REFRESH_ALERT_THRESHOLD', '100'))
        self.failure_alert_threshold = int(os.environ.get('FAILURE_ALERT_THRESHOLD', '3'))
        self.token_age_alert_days = int(os.environ.get('TOKEN_AGE_ALERT_DAYS', '60'))
        
        # Notification settings
        self.enable_notifications = os.environ.get('ENABLE_NOTIFICATIONS', 'true').lower() == 'true'
        
        # Set base URLs based on environment
        if self.environment == 'production':
            self.oauth_base = 'https://login.procore.com'
            self.api_base = 'https://api.procore.com'
        else:  # sandbox
            self.oauth_base = 'https://sandbox.procore.com'
            self.api_base = 'https://sandbox.procore.com'
        
        # Load existing tokens
        if self.access_token:
            logger.info(f"Loaded access token from environment (length: {len(self.access_token)})")
            self.token_expires_at = datetime.now() + timedelta(minutes=5)
        
        if self.refresh_token:
            logger.info(f"Loaded refresh token from environment (length: {len(self.refresh_token)})")

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
            
            # Reset monitoring counters for new authentication
            self.access_token = token_data['access_token']
            self.refresh_token = token_data.get('refresh_token')
            expires_in = token_data.get('expires_in', 7200)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            # Reset monitoring tracking
            self.refresh_count = 0
            self.token_created_at = datetime.now()
            self.consecutive_failures = 0
            self.last_refresh_success = datetime.now()
            self.last_refresh_failure = None
            
            logger.info(f"NEW_ACCESS_TOKEN: {self.access_token}")
            logger.info(f"NEW_REFRESH_TOKEN: {self.refresh_token}")
            logger.info(f"🔄 Authentication successful - Ready for PO processing with UI updates")
            
            return True
            
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False
    
    def _refresh_access_token(self) -> bool:
        """Enhanced refresh with comprehensive monitoring"""
        
        if not self.refresh_token:
            logger.error("No refresh token available")
            self._record_refresh_failure("No refresh token available")
            return False
            
        url = f"{self.oauth_base}/oauth/token"
        data = {
            'grant_type': 'refresh_token',
            'client_id': PROCORE_CLIENT_ID,
            'client_secret': PROCORE_CLIENT_SECRET,
            'refresh_token': self.refresh_token
        }
        
        try:
            logger.info(f"Token refresh attempt #{self.refresh_count + 1}")
            response = requests.post(url, data=data)
            
            if response.status_code == 200:
                token_data = response.json()
                
                # Update tokens
                self.access_token = token_data['access_token']
                self.refresh_token = token_data.get('refresh_token', self.refresh_token)
                expires_in = token_data.get('expires_in', 7200)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                # Record successful refresh
                self._record_refresh_success()
                
                logger.info(f"✅ Token refresh successful (#{self.refresh_count})")
                return True
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                logger.error(f"Token refresh failed: {error_msg}")
                self._record_refresh_failure(error_msg)
                return False
            
        except Exception as e:
            error_msg = f"Exception: {str(e)}"
            logger.error(f"Error refreshing token: {error_msg}")
            self._record_refresh_failure(error_msg)
            return False
    
    def _record_refresh_success(self):
        """Record successful refresh for monitoring"""
        self.refresh_count += 1
        self.consecutive_failures = 0
        self.last_refresh_success = datetime.now()
    
    def _record_refresh_failure(self, error_msg: str):
        """Record failed refresh for monitoring"""
        self.consecutive_failures += 1
        self.last_refresh_failure = datetime.now()
        
        if self.consecutive_failures >= self.failure_alert_threshold:
            self._send_admin_notification(
                f"🚨 HIGH PRIORITY: {self.consecutive_failures} consecutive token refresh failures. "
                f"Last error: {error_msg}"
            )
    
    def _send_admin_notification(self, message: str):
        """Send notification to administrators"""
        
        if not self.enable_notifications:
            logger.info(f"📧 NOTIFICATION (disabled): {message}")
            return
            
        webhook_url = os.environ.get('SLACK_WEBHOOK_URL')
        if webhook_url:
            try:
                payload = {
                    "text": f"🤖 Procore PO Automation: {message}",
                    "channel": "#procore-automation",
                    "username": "PO Automation"
                }
                requests.post(webhook_url, json=payload)
                logger.info("📧 Admin notification sent to Slack")
            except Exception as e:
                logger.error(f"Failed to send Slack notification: {e}")
        else:
            logger.info(f"📧 NOTIFICATION: {message}")
    
    def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid access token, refreshing if necessary"""
        if not self.access_token:
            logger.error("No access token available")
            return False
            
        # Check if token is expired or about to expire
        if self.token_expires_at and datetime.now() >= self.token_expires_at:
            logger.info("Access token is expired, attempting to refresh")
            return self._refresh_access_token()
        
        return True
    
    def _make_authenticated_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an authenticated request with automatic token refresh"""
        if not self._ensure_valid_token():
            raise Exception("Unable to obtain valid access token")
        
        headers = kwargs.get('headers', {})
        headers['Authorization'] = f'Bearer {self.access_token}'
        kwargs['headers'] = headers
        
        response = requests.request(method, url, **kwargs)
        
        # Handle 401 (token expired) with automatic refresh
        if response.status_code == 401:
            logger.warning("Received 401 Unauthorized, attempting token refresh")
            if self._refresh_access_token():
                # Retry request with new token
                headers['Authorization'] = f'Bearer {self.access_token}'
                response = requests.request(method, url, **kwargs)
                logger.info("Request retried successfully after token refresh")
            else:
                logger.error("Token refresh failed, request cannot be retried")
        
        return response

    # ===== CORE PO PROCESSING METHODS (from working code) =====
    
    def get_purchase_order_by_id(self, resource_id: str, project_id: str, company_id: str) -> Optional[Dict]:
        """Get specific PO using filters (from working code)"""
        url = f"{self.api_base}/rest/v1.0/purchase_order_contracts"
        headers = {
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
            response = self._make_authenticated_request('GET', url, headers=headers, params=params)
            response.raise_for_status()
            po_data = response.json()
            
            if not po_data or len(po_data) == 0:
                logger.error(f"PO {resource_id} not found")
                return None
                
            return po_data[0]  # Return first (should be only) result
            
        except Exception as e:
            logger.error(f"Failed to get PO {resource_id}: {e}")
            self.processing_errors += 1
            return None
    
    def get_po_line_items(self, resource_id: str, project_id: str, company_id: str) -> Optional[List[Dict]]:
        """Get PO line items (from working code)"""
        url = f"{self.api_base}/rest/v1.0/purchase_order_contracts/{resource_id}/line_items"
        headers = {
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'project_id': project_id}
        
        try:
            logger.info(f"Getting line items for PO {resource_id}")
            response = self._make_authenticated_request('GET', url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Failed to get line items for PO {resource_id}: {e}")
            return None
    
    def get_budget_views(self, project_id: str, company_id: str) -> Optional[List[Dict]]:
        """Get budget views (from working code)"""
        url = f"{self.api_base}/rest/v1.0/budget_views"
        headers = {
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'project_id': project_id}
        
        try:
            logger.info(f"Getting budget views for project {project_id}")
            response = self._make_authenticated_request('GET', url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Failed to get budget views: {e}")
            return None
    
    def get_budget_detail_rows(self, budget_view_id: str, project_id: str, company_id: str) -> Optional[List[Dict]]:
        """Get budget detail rows (from working code)"""
        url = f"{self.api_base}/rest/v1.0/budget_views/{budget_view_id}/detail_rows"
        headers = {
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'project_id': project_id}
        
        try:
            logger.info(f"Getting budget detail rows for view {budget_view_id}")
            response = self._make_authenticated_request('GET', url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logger.error(f"Failed to get budget detail rows: {e}")
            return None

    # ===== CRITICAL UI UPDATE METHOD (from working code) =====
    
    def update_po_tiers(self, resource_id: str, project_id: str, company_id: str, tier: str) -> bool:
        """Update PO with tier checkboxes - THIS IS WHAT UPDATES THE UI"""
        url = f"{self.api_base}/rest/v1.0/purchase_order_contracts/{resource_id}"
        headers = {
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'run_configurable_validations': 'true'}
        
        # Set only the target tier to true, others to false - THIS TRIGGERS UI REFRESH
        custom_fields = {}
        for tier_name, field_id in TIER_FIELD_IDS.items():
            custom_fields[f"custom_field_{field_id}"] = 'true' if tier_name == tier else 'false'
        
        payload = {
            "project_id": int(project_id),
            "purchase_order_contract": custom_fields
        }
        
        try:
            logger.info(f"🔄 Updating PO {resource_id} with {tier} - THIS WILL UPDATE THE UI")
            response = self._make_authenticated_request('PATCH', url, headers=headers, params=params, json=payload)
            response.raise_for_status()
            
            logger.info(f"✅ Updated PO {resource_id} to {tier} - UI should now show changes")
            self.approval_updates += 1
            self.ui_updates += 1
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to update PO {resource_id}: {e}")
            self.processing_errors += 1
            return False

    def add_po_log(self, resource_id: str, project_id: str, company_id: str, 
                   tier: str, reason: str, webhook_timestamp: str,
                   po_data: Dict = None, budget_analysis: Dict = None,
                   has_potential_change_orders: bool = False, 
                   unallocated_cost_present: bool = False) -> bool:
        """Add justification log to PO after tier assignment (from working code)"""
        url = f"{self.api_base}/rest/v1.0/purchase_order_contracts/{resource_id}"
        headers = {
            'Procore-Company-Id': str(company_id),
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        params = {'run_configurable_validations': 'true'}
        
        try:
            # Convert webhook timestamp to Australian Eastern Time
            from datetime import datetime, timezone, timedelta
            utc_time = datetime.fromisoformat(webhook_timestamp.replace('Z', '+00:00'))
            # AEST is UTC+10 (or UTC+11 during daylight saving)
            aest_time = utc_time.astimezone(timezone(timedelta(hours=10)))
            timestamp_str = aest_time.strftime('%Y-%m-%d %H:%M:%S AEST')
            
            # Get PO amount
            grand_total = float(po_data.get('grand_total', 0) or 0) if po_data else 0.0
            
            # Build justification log with proper line breaks
            log_lines = [
                f"Outcome: {tier}",
                f"Timestamp: {timestamp_str}",
                "Justification:",
                f"- PO Amount: ${grand_total:,.2f}",
                f"- Has Potential Change Orders: {'✅ Yes' if has_potential_change_orders else '❌ No'}",
                f"- Unallocated Cost Present: {'✅ Yes' if unallocated_cost_present else '❌ No'}"
            ]
            
            # Add budget analysis details
            if budget_analysis and budget_analysis.get('budget_analysis'):
                log_lines.append("- Budget Analysis:")
                for budget_item in budget_analysis['budget_analysis']:
                    wbs_description = budget_item.get('wbs_description', 'Unknown')
                    sum_wbs_code_line_items = budget_item.get('sum_wbs_code_line_items', 0)
                    committed_costs = budget_item.get('committed_costs', 0)
                    revised_budget = budget_item.get('revised_budget', 0)
                    future_committed_costs = budget_item.get('future_committed_costs', 0)
                    is_overbudget = budget_item.get('is_overbudget', False)
                    
                    status_emoji = "❌ OVER BUDGET" if is_overbudget else "✅ Within Budget"
                    log_lines.append(f"  {wbs_description}: ${committed_costs:,.2f} committed + ${sum_wbs_code_line_items:,.2f} PO = ${future_committed_costs:,.2f} vs ${revised_budget:,.2f} revised budget ({status_emoji})")
            
            # Add tier-specific reasoning
            log_lines.append(f"- Decision: {reason}")
            
            # If error case, add error details
            if tier == ApprovalTier.TIER_4 and "error" in reason.lower():
                log_lines.append("- Note: Assigned highest tier for safety due to processing error")
            
            # Join all lines with newline characters
            justification_text = "\n".join(log_lines)
            
            # Use justification field (update field ID if different)
            payload = {
                "project_id": int(project_id),
                "purchase_order_contract": {
                    "custom_field_4367": justification_text  # Update this field ID if needed
                }
            }
            
            logger.info(f"📝 Adding justification log to PO {resource_id}")
            
            response = self._make_authenticated_request('PATCH', url, headers=headers, params=params, json=payload)
            response.raise_for_status()
            
            logger.info(f"✅ Successfully added justification log to PO {resource_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to add log to PO {resource_id}: {e}")
            return False

    # ===== BUSINESS LOGIC ENGINE (from working code) =====
    
    def calculate_approval_tier(self, project_id: str, resource_id: str, company_id: str, webhook_timestamp: str = None) -> Tuple[str, str]:
        """Calculate approval tier using updated business logic (from working code)"""
        try:
            logger.info(f"🔄 Processing approval logic for PO {resource_id}")
            logger.info(f"Keys identified: company_id: {company_id}, project_id: {project_id}, resource_id: {resource_id}")
            
            # Step 3: Purchase Order Data Extraction
            logger.info("=== STEP 3: PO DATA EXTRACTION ===")
            po_data = self.get_purchase_order_by_id(resource_id, project_id, company_id)
            if not po_data:
                logger.error(f"Could not retrieve PO {resource_id}")
                error_reason = f"Could not retrieve PO {resource_id}"
                if webhook_timestamp:
                    self.add_po_log(resource_id, project_id, company_id, 
                                  ApprovalTier.TIER_4, error_reason, webhook_timestamp)
                return ApprovalTier.TIER_4, error_reason
            
            grand_total = float(po_data.get('grand_total', 0) or 0)
            has_potential_change_orders = bool(po_data.get('has_potential_change_orders', False))
            
            logger.info(f"{resource_id}_PO_data: Grand_total={grand_total}, has_potential_change_orders={has_potential_change_orders}")
            
            # Step 4: Line Items Analysis
            logger.info("=== STEP 4: LINE ITEMS ANALYSIS ===")
            line_items = self.get_po_line_items(resource_id, project_id, company_id)
            if not line_items:
                logger.error(f"Could not retrieve line items for PO {resource_id}")
                error_reason = f"Could not retrieve line items for PO {resource_id}"
                if webhook_timestamp:
                    self.add_po_log(resource_id, project_id, company_id, 
                                  ApprovalTier.TIER_4, error_reason, webhook_timestamp,
                                  po_data=po_data)
                return ApprovalTier.TIER_4, error_reason
            
            # Process line items and group by wbs_code
            line_items_by_wbs = defaultdict(list)
            unallocated_cost_present = False
            
            for item in line_items:
                line_item_id = item.get('id')
                po_line_item_id = f"{resource_id}_{line_item_id}"
                amount = float(item.get('amount', 0) or 0)
                wbs_code = item.get('wbs_code', {})
                cost_code = item.get('cost_code', {})
                
                # Check for unallocated costs (update this ID if different)
                cost_code_id = cost_code.get('id') if isinstance(cost_code, dict) else None
                if cost_code_id == 9427186:  # Update this ID for your unallocated cost code
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
            budget_views = self.get_budget_views(project_id, company_id)
            if not budget_views:
                logger.error("Could not get budget views")
                error_reason = "Could not get budget views"
                if webhook_timestamp:
                    self.add_po_log(resource_id, project_id, company_id, 
                                  ApprovalTier.TIER_4, error_reason, webhook_timestamp,
                                  po_data=po_data, 
                                  has_potential_change_orders=has_potential_change_orders,
                                  unallocated_cost_present=unallocated_cost_present)
                return ApprovalTier.TIER_4, error_reason
            
            budget_view_id = budget_views[0]['id']
            logger.info(f"budget_view_id: {budget_view_id}")
            
            # Step 6: Budget View Detail Rows Retrieval
            logger.info("=== STEP 6: BUDGET DATA RECEIVED ===")
            budget_rows = self.get_budget_detail_rows(budget_view_id, project_id, company_id)
            if not budget_rows:
                logger.error("Could not get budget detail rows")
                error_reason = "Could not get budget detail rows"
                if webhook_timestamp:
                    self.add_po_log(resource_id, project_id, company_id, 
                                  ApprovalTier.TIER_4, error_reason, webhook_timestamp,
                                  po_data=po_data,
                                  has_potential_change_orders=has_potential_change_orders,
                                  unallocated_cost_present=unallocated_cost_present)
                return ApprovalTier.TIER_4, error_reason
            
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
            budget_analysis_data = []
            
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
                    error_reason = f"No matching budget row found for wbs_code: {wbs_key}"
                    if webhook_timestamp:
                        self.add_po_log(resource_id, project_id, company_id, 
                                      ApprovalTier.TIER_4, error_reason, webhook_timestamp,
                                      po_data=po_data,
                                      has_potential_change_orders=has_potential_change_orders,
                                      unallocated_cost_present=unallocated_cost_present)
                    return ApprovalTier.TIER_4, error_reason
                
                # Calculate future committed costs
                revised_budget = budget_data['revised_budget']
                committed_costs = budget_data['committed_costs']
                future_committed_costs = committed_costs + total_po_amount
                is_overbudget = future_committed_costs > revised_budget
                
                # Store budget analysis data for logging
                budget_analysis_data.append({
                    'wbs_code': wbs_key,
                    'wbs_description': wbs_description,
                    'sum_wbs_code_line_items': total_po_amount,
                    'committed_costs': committed_costs,
                    'revised_budget': revised_budget,
                    'future_committed_costs': future_committed_costs,
                    'is_overbudget': is_overbudget
                })
                
                if is_overbudget:
                    is_any_overbudget = True
                    overbudget_details.append(f"{wbs_description}: ${future_committed_costs} > ${revised_budget}")
                    logger.info(f"wbs_code {wbs_description} is over budget. CALCULATION: is_overbudget = true")
                else:
                    logger.info(f"wbs_code {wbs_description} isn't over budget. CALCULATION: is_overbudget = false")
            
            # Prepare budget analysis for logging
            budget_analysis = {'budget_analysis': budget_analysis_data}
            
            # Step 8: Business Logic - Tier Assignment
            logger.info("=== BUSINESS_LOGIC: APPROVAL_TIER_CALCULATION ===")
            
            tier = None
            reason = None
            
            # Tier 4 Conditions (Highest Priority)
            if is_any_overbudget:
                tier = ApprovalTier.TIER_4
                reason = f"Over budget on wbs_code level: {'; '.join(overbudget_details)}"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_4_CONDITION_1_TRIGGERED: is_over_budget=True")
            elif has_potential_change_orders and unallocated_cost_present:
                tier = ApprovalTier.TIER_4
                reason = "Has Potential Change Order and Unallocated Cost"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_4_CONDITION_2_TRIGGERED")
            # Tier 3 Conditions
            elif has_potential_change_orders:
                tier = ApprovalTier.TIER_3
                reason = "Has potential change order=true"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_3_CONDITION_1_TRIGGERED")
            # Tier 2 Conditions
            elif unallocated_cost_present:
                tier = ApprovalTier.TIER_2
                reason = "Unallocated cost code"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_2_CONDITION_1_TRIGGERED")
            elif grand_total > 10000:
                tier = ApprovalTier.TIER_2
                reason = "Grand total > $10,000"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_2_CONDITION_2_TRIGGERED")
            # Tier 1 Conditions
            elif 5000 <= grand_total <= 10000:
                tier = ApprovalTier.TIER_1
                reason = "Grand total between $5,000-$10,000"
                logger.info("BUSINESS_LOGIC_DECISION: TIER_1_CONDITION_1_TRIGGERED")
            # Auto-Approve (Default)
            else:
                tier = ApprovalTier.AUTO_APPROVE
                reason = "Grand total < $5,000"
                logger.info("BUSINESS_LOGIC_DECISION: AUTO_APPROVE_DEFAULT_TRIGGERED")
            
            # Add justification log if webhook timestamp is provided
            if webhook_timestamp:
                self.add_po_log(resource_id, project_id, company_id, tier, reason, webhook_timestamp,
                              po_data=po_data, budget_analysis=budget_analysis,
                              has_potential_change_orders=has_potential_change_orders,
                              unallocated_cost_present=unallocated_cost_present)
            
            return tier, reason
            
        except Exception as e:
            logger.error(f"Error calculating approval tier: {e}")
            error_reason = f"Error in calculation: {e}"
            if webhook_timestamp:
                self.add_po_log(resource_id, project_id, company_id, 
                              ApprovalTier.TIER_4, error_reason, webhook_timestamp)
            return ApprovalTier.TIER_4, error_reason
    
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

    # ===== COMPLETE WEBHOOK PROCESSING (with UI updates) =====
    
    def process_purchase_order_webhook(self, payload: ProcoreWebhookPayload) -> bool:
        """Process a Purchase Order webhook event with UI updates"""
        try:
            logger.info(f"🔔 Processing PO webhook: {payload.resource_type} - {payload.reason}")
            
            # Extract IDs
            company_id = payload.company_id
            project_id = payload.project_id
            po_id = payload.resource_id
            
            # Only process create and update events
            if payload.reason not in ['create', 'update']:
                logger.info(f"Skipping {payload.reason} event")
                return True
            
            # Check authentication
            if not self._ensure_valid_token():
                logger.error("Not authenticated - cannot process webhook")
                return False
            
            # Calculate approval tier (includes logging)
            tier, reason = self.calculate_approval_tier(
                project_id, 
                po_id, 
                company_id,
                payload.timestamp
            )
            
            # **THIS IS THE KEY** - Update PO with tier checkboxes (triggers UI refresh)
            ui_update_success = self.update_po_tiers(po_id, project_id, company_id, tier)
            
            if ui_update_success:
                logger.info(f"✅ Successfully updated PO {po_id} to {tier} - UI should be updated")
                self.webhooks_processed += 1
                self.pos_processed += 1
                
                # Send notification for significant events
                if tier == ApprovalTier.AUTO_APPROVE:
                    self._send_admin_notification(
                        f"🚀 PO Auto-Approved: PO {po_id} (${tier}) - {reason}"
                    )
                elif tier == ApprovalTier.TIER_4:
                    self._send_admin_notification(
                        f"🚨 High-Tier PO: PO {po_id} assigned to {tier} - {reason}"
                    )
                
                return True
            else:
                logger.error(f"❌ Failed to update PO {po_id} with {tier}")
                self.processing_errors += 1
                return False
            
        except Exception as e:
            logger.error(f"❌ Error processing PO webhook: {e}")
            self.processing_errors += 1
            return False
    
    def get_monitoring_status(self) -> Dict:
        """Get comprehensive monitoring status including UI update tracking"""
        
        token_age = datetime.now() - self.token_created_at
        
        # Calculate health score
        health_score = 100
        if self.consecutive_failures > 0:
            health_score -= min(50, self.consecutive_failures * 10)
        
        # Factor in processing errors
        if self.processing_errors > 0 and self.webhooks_processed > 0:
            error_rate = (self.processing_errors / self.webhooks_processed) * 100
            health_score -= min(30, error_rate)
        
        health_score = max(0, health_score)
        
        return {
            'status': 'authenticated' if self.access_token else 'not_authenticated',
            'environment': self.environment,
            'token_expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None,
            
            # Basic token info
            'token_preview': f"{self.access_token[:20]}...{self.access_token[-10:]}" if self.access_token and len(self.access_token) > 30 else self.access_token,
            'token_length': len(self.access_token) if self.access_token else 0,
            'full_token': self.access_token,
            'refresh_token': self.refresh_token,
            
            # Processing statistics
            'webhooks_processed': self.webhooks_processed,
            'pos_processed': self.pos_processed,
            'approval_updates': self.approval_updates,
            'ui_updates': self.ui_updates,  # NEW: Track UI updates
            'processing_errors': self.processing_errors,
            'processing_success_rate': round((self.pos_processed / max(1, self.webhooks_processed)) * 100, 2),
            'ui_update_rate': round((self.ui_updates / max(1, self.pos_processed)) * 100, 2),  # NEW
            
            # Token monitoring
            'total_refreshes': self.refresh_count,
            'consecutive_failures': self.consecutive_failures,
            'token_age_days': token_age.days,
            'token_created_at': self.token_created_at.isoformat(),
            
            # Health indicators
            'health_score': health_score,
            'health_status': self._get_health_status(health_score),
            
            # Configuration
            'tier_field_ids': TIER_FIELD_IDS,
            'refresh_alert_threshold': self.refresh_alert_threshold,
            'failure_alert_threshold': self.failure_alert_threshold,
            'token_age_alert_days': self.token_age_alert_days,
            'notifications_enabled': self.enable_notifications,
            
            # Recommendations
            'recommendations': self._get_recommendations(),
            'alerts': self._get_current_alerts()
        }
    
    def _get_health_status(self, score: int) -> str:
        """Get health status based on score"""
        if score >= 90:
            return "EXCELLENT"
        elif score >= 75:
            return "GOOD"
        elif score >= 50:
            return "FAIR"
        elif score >= 25:
            return "POOR"
        else:
            return "CRITICAL"
    
    def _get_recommendations(self) -> List[str]:
        """Get system recommendations"""
        recommendations = []
        
        if self.consecutive_failures > 0:
            recommendations.append(f"Monitor authentication - {self.consecutive_failures} recent failures")
        
        if self.processing_errors > 0:
            error_rate = (self.processing_errors / max(1, self.webhooks_processed)) * 100
            if error_rate > 10:
                recommendations.append(f"High error rate: {error_rate:.1f}% - Review processing logs")
        
        if self.webhooks_processed == 0:
            recommendations.append("No webhooks processed yet - System ready for PO automation")
        
        if self.ui_updates < self.pos_processed:
            ui_update_rate = (self.ui_updates / max(1, self.pos_processed)) * 100
            if ui_update_rate < 90:
                recommendations.append(f"UI update rate: {ui_update_rate:.1f}% - Some POs may not show tier changes")
        
        if not recommendations:
            recommendations.append("All systems operating normally - UI updates working")
            
        return recommendations
    
    def _get_current_alerts(self) -> List[str]:
        """Get current alert conditions"""
        alerts = []
        
        if self.consecutive_failures >= self.failure_alert_threshold:
            alerts.append(f"HIGH: {self.consecutive_failures} consecutive authentication failures")
        
        if self.processing_errors > 5:
            alerts.append(f"MEDIUM: {self.processing_errors} processing errors detected")
        
        if self.ui_updates < self.pos_processed:
            missing_ui_updates = self.pos_processed - self.ui_updates
            if missing_ui_updates > 3:
                alerts.append(f"MEDIUM: {missing_ui_updates} POs processed without UI updates")
        
        return alerts

# Global instances
api_client = UIUpdatingProcoreAPIClient()

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

# ===== FLASK ROUTES =====

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return 'Procore PO Automation Service - UI Updates Active', 200

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
        logger.info("OAuth authentication successful - Ready for PO processing with UI updates")
        return 'Authentication successful! Purchase Order automation with UI updates is now active.', 200
    else:
        return 'Authentication failed', 500

@app.route('/auth/status', methods=['GET'])
def auth_status():
    """Check authentication status"""
    try:
        if api_client.access_token and api_client._ensure_valid_token():
            monitoring_status = api_client.get_monitoring_status()
            
            return jsonify({
                'status': monitoring_status['status'],
                'expires_at': monitoring_status['token_expires_at'],
                'environment': monitoring_status['environment'],
                'token_preview': monitoring_status['token_preview'],
                'token_length': monitoring_status['token_length'],
                'full_token': monitoring_status['full_token'],
                'refresh_token': monitoring_status['refresh_token'],
                'processing_stats': {
                    'webhooks_processed': monitoring_status['webhooks_processed'],
                    'pos_processed': monitoring_status['pos_processed'],
                    'ui_updates': monitoring_status['ui_updates'],
                    'success_rate': monitoring_status['processing_success_rate'],
                    'ui_update_rate': monitoring_status['ui_update_rate']
                }
            }), 200
        else:
            return jsonify({
                'status': 'not_authenticated',
                'oauth_url': f"{api_client.oauth_base}/oauth/authorize?client_id={PROCORE_CLIENT_ID}&response_type=code&redirect_uri={PROCORE_REDIRECT_URI}"
            }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/auth/monitoring-status', methods=['GET'])
def monitoring_status():
    """Get detailed monitoring status"""
    try:
        status = api_client.get_monitoring_status()
        return jsonify(status), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/config/tier-fields', methods=['GET'])
def get_tier_fields():
    """Get current tier field configuration"""
    return jsonify({
        'tier_field_ids': TIER_FIELD_IDS,
        'environment': PROCORE_ENVIRONMENT,
        'note': 'These field IDs should match your Procore custom field IDs for tier checkboxes'
    }), 200

@app.route('/test/process-po', methods=['POST'])
def test_process_po():
    """Test endpoint to manually process a PO with UI updates"""
    try:
        data = request.get_json()
        company_id = data.get('company_id')
        project_id = data.get('project_id')
        po_id = data.get('po_id')
        
        if not all([company_id, project_id, po_id]):
            return jsonify({'error': 'Missing required parameters: company_id, project_id, po_id'}), 400
        
        # Create test webhook payload
        test_payload = ProcoreWebhookPayload(
            id='test-' + str(datetime.now().timestamp()),
            timestamp=datetime.now().isoformat(),
            reason='test',
            company_id=company_id,
            project_id=project_id,
            user_id='test',
            resource_type='Purchase Order Contracts',
            resource_id=po_id,
            payload_version='1.0'
        )
        
        # Process the PO with UI updates
        result = api_client.process_purchase_order_webhook(test_payload)
        
        return jsonify({
            'test_name': 'manual_po_processing_with_ui_updates',
            'timestamp': datetime.now().isoformat(),
            'success': result,
            'po_id': po_id,
            'monitoring_status': api_client.get_monitoring_status()
        }), 200
        
    except Exception as e:
        return jsonify({
            'test_name': 'manual_po_processing_with_ui_updates',
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }), 500

@app.route('/', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Handle Procore webhook with UI updates"""
    try:
        logger.info("🔔 Webhook received")
        
        # Validate JSON payload
        if not request.is_json:
            logger.warning("Non-JSON webhook received")
            return 'OK', 200
        
        json_data = request.get_json(silent=True)
        if not json_data:
            logger.warning("Invalid JSON payload")
            return 'OK', 200
        
        # Parse webhook payload
        payload = parse_webhook_payload(json_data)
        if not payload:
            logger.warning("Failed to parse webhook payload")
            return 'OK', 200
        
        # Only process PO events
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
        
        # Update the payload with the correct PO ID
        payload.resource_id = po_id
        
        logger.info(f"🔄 Processing webhook: PO {po_id} - {payload.reason}")
        
        # **PROCESS WITH UI UPDATES**
        success = api_client.process_purchase_order_webhook(payload)
        
        if success:
            logger.info(f"✅ Successfully processed webhook for PO {po_id} - UI should be updated")
        else:
            logger.error(f"❌ Failed to process webhook for PO {po_id}")
        
        # Always return OK to Procore
        return 'OK', 200
        
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}")
        api_client.processing_errors += 1
        return 'OK', 200

if __name__ == '__main__':
    # Validate environment variables
    required_vars = ['PROCORE_CLIENT_ID', 'PROCORE_CLIENT_SECRET', 'PROCORE_REDIRECT_URI']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        sys.exit(1)
    
    logger.info(f"🚀 Starting Procore PO Automation Service with UI Updates")
    logger.info(f"Environment: {PROCORE_ENVIRONMENT}")
    logger.info(f"OAuth callback: {PROCORE_REDIRECT_URI}")
    logger.info(f"Tier Field IDs: {TIER_FIELD_IDS}")
    logger.info(f"🎯 Key Feature: UI updates via tier checkbox updates")
    logger.info(f"🔧 Ready to process Purchase Order webhooks with live UI updates")
    
    # Get port from environment variable (Cloud Run requirement)
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Starting server on port {port}")
    
    app.run(debug=False, host='0.0.0.0', port=port)
    
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
