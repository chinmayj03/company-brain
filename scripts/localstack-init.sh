#!/bin/sh
# Runs inside LocalStack on first start.
# Creates the SQS queues needed by the ingestion pipeline.

set -e
echo "LocalStack init: creating SQS queues..."

awslocal sqs create-queue \
  --queue-name company-brain-ingestion \
  --attributes '{"VisibilityTimeout":"60","MessageRetentionPeriod":"1209600"}'

awslocal sqs create-queue \
  --queue-name company-brain-ingestion-dlq \
  --attributes '{"MessageRetentionPeriod":"1209600"}'

# Wire DLQ to main queue (max 3 receive attempts before DLQ)
MAIN_URL=$(awslocal sqs get-queue-url --queue-name company-brain-ingestion --query QueueUrl --output text)
DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url $(awslocal sqs get-queue-url --queue-name company-brain-ingestion-dlq --query QueueUrl --output text) \
  --attribute-names QueueArn \
  --query Attributes.QueueArn --output text)

awslocal sqs set-queue-attributes \
  --queue-url "$MAIN_URL" \
  --attributes "{\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"

echo "LocalStack init: queues ready"
awslocal sqs list-queues
