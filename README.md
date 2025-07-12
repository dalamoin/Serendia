Build the Container

gcloud builds submit --config cloudbuild.yaml --project=serendia

Build, Deploy and Route Traffic

source .env && \
TIMESTAMP=$(date +%s) && \
gcloud builds submit --config cloudbuild.yaml --project=serendia && \
gcloud run deploy po-automation \
  --image us-central1-docker.pkg.dev/serendia/po-automation-repo/po-automation:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 10 \
  --concurrency 80 \
  --set-env-vars="PROCORE_CLIENT_ID=${PROCORE_CLIENT_ID},PROCORE_CLIENT_SECRET=${PROCORE_CLIENT_SECRET},PROCORE_REDIRECT_URI=${PROCORE_REDIRECT_URI},PROCORE_ENVIRONMENT=${PROCORE_ENVIRONMENT},DEPLOY_TIME=${TIMESTAMP}" \
  --no-traffic \
  --project serendia && \
gcloud run services update-traffic po-automation \
  --to-latest \
  --region=us-central1 \
  --project=serendia

Log the Payloads

gcloud beta logging tail \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="po-automation" AND (textPayload:"✅" OR textPayload:"📩" OR textPayload:"📦" OR textPayload:"📄" OR textPayload:"⚠️")' \
  --project=serendia
