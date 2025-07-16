# Step 1: Deploy Image

# Switch to correct project and directory
gcloud config set project serendia
cd ~/projects/serendia/po-automation
source .env

#Build new image with the fix
TIMESTAMP=$(date +%s)
echo "Building token refresh fix image: ${TIMESTAMP}"
gcloud builds submit --config cloudbuild.yaml --substitutions=_TIMESTAMP=${TIMESTAMP} --project=serendia

#Verify build completed
gcloud container images list-tags us-central1-docker.pkg.dev/serendia/po-automation-repo/po-automation --limit=3

#Use the newly built image
LATEST_IMAGE_TAG=${TIMESTAMP}
echo "Using image tag: ${LATEST_IMAGE_TAG}"

#Deploy with the new image (no tokens yet)
gcloud run deploy po-automation \
  --image us-central1-docker.pkg.dev/serendia/po-automation-repo/po-automation:${LATEST_IMAGE_TAG} \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 10 \
  --concurrency 80 \
  --set-env-vars="PROCORE_CLIENT_ID=${PROCORE_CLIENT_ID},PROCORE_CLIENT_SECRET=${PROCORE_CLIENT_SECRET},PROCORE_REDIRECT_URI=${PROCORE_REDIRECT_URI},PROCORE_ENVIRONMENT=${PROCORE_ENVIRONMENT},DEPLOY_TIME=${LATEST_IMAGE_TAG}" \
  --project=serendia

# Step 2: Authetnicate

#Authenticate

echo "Visit this URL to authenticate:"
echo "https://sandbox.procore.com/oauth/authorize?client_id=${PROCORE_CLIENT_ID}&response_type=code&redirect_uri=https://po-automation-68642982777.us-central1.run.app/oauth/callback"

# Step 3: Acquire Tokens

#Get Fresh Tokens

curl -s https://po-automation-68642982777.us-central1.run.app/auth/status | python3 -c "
import sys, json
data = json.load(sys.stdin)
if data.get('status') == 'authenticated':
    print('✅ Authentication successful!')
    print()
    print('ACCESS_TOKEN=' + data.get('full_token', ''))
    print('REFRESH_TOKEN=' + data.get('refresh_token', ''))
else:
    print('❌ Status:', data.get('status'))
    if 'oauth_url' in data:
        print('OAuth URL:', data['oauth_url'])
"

# Step 4: Redploy with automatic refresh token deployment

#Replace YOUR_ACCESS_TOKEN and YOUR_REFRESH_TOKEN with actual values

gcloud run deploy po-automation --image us-central1-docker.pkg.dev/serendia/po-automation-repo/po-automation:${LATEST_IMAGE_TAG} --platform managed --region us-central1 --allow-unauthenticated --memory 512Mi --cpu 1 --timeout 300 --max-instances 10 --concurrency 80 --set-env-vars="PROCORE_CLIENT_ID=${PROCORE_CLIENT_ID},PROCORE_CLIENT_SECRET=${PROCORE_CLIENT_SECRET},PROCORE_REDIRECT_URI=${PROCORE_REDIRECT_URI},PROCORE_ENVIRONMENT=${PROCORE_ENVIRONMENT},PROCORE_ACCESS_TOKEN=YOUR_ACCESS_TOKEN_HERE,PROCORE_REFRESH_TOKEN=YOUR_REFRESH_TOKEN_HERE,DEPLOY_TIME=${LATEST_IMAGE_TAG}" --project=serendia
