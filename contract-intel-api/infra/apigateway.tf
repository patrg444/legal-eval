# --- API Gateway REST API ---
resource "aws_api_gateway_rest_api" "main" {
  name        = "${var.project_name}-contract-api"
  description = "API for Contract Intelligence service"

  # Endpoint configuration for PRIVATE API
  # This requires a VPC endpoint for 'execute-api' to be associated.
  endpoint_configuration {
    types = ["PRIVATE"]
    # vpc_endpoint_ids = [aws_vpc_endpoint.api_gateway.id] # From network.tf
    # Ensure aws_vpc_endpoint.api_gateway is defined in network.tf for this to work.
    # The count = 1 in network.tf for api_gateway endpoint means it's always created.
    # If it was conditional, this reference would need to be conditional too.
    # Using try to avoid errors if the endpoint is not found (e.g. during partial applies or if conditional)
    vpc_endpoint_ids = [try(aws_vpc_endpoint.api_gateway[0].id, null)]
  }

  # API Gateway policy to restrict access, e.g., only from specific VPC or VPC endpoint
  # This is crucial for private APIs.
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect    = "Allow",
        Principal = "*", # Or specify AWS account, role, user
        Action    = "execute-api:Invoke",
        Resource  = "arn:aws:execute-api:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${aws_api_gateway_rest_api.main.id}/*",
        # Condition to restrict to source VPC endpoint
        Condition = {
          StringEquals = {
            "aws:sourceVpce" = try(aws_vpc_endpoint.api_gateway[0].id, "")
          }
        }
      },
      # You might need another statement to deny all traffic that does NOT come from the VPCE
      # {
      #   "Effect": "Deny",
      #   "Principal": "*",
      #   "Action": "execute-api:Invoke",
      #   "Resource": "arn:aws:execute-api:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${aws_api_gateway_rest_api.main.id}/*",
      #   "Condition": {
      #     "StringNotEquals": {
      #       "aws:sourceVpce": try(aws_vpc_endpoint.api_gateway[0].id, "")
      #     }
      #   }
      # }
    ]
  })


  tags = {
    Name        = "${var.project_name}-api-gateway"
    Project     = var.project_name
    Environment = "dev"
  }
}

# --- API Gateway Resource: /v1 ---
resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "v1"
}

# --- API Gateway Resource: /v1/contracts ---
resource "aws_api_gateway_resource" "contracts" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "contracts"
}

# --- API Gateway Method: POST /v1/contracts ---
resource "aws_api_gateway_method" "contracts_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.contracts.id
  http_method   = "POST"
  authorization = "NONE" # Or "AWS_IAM", "CUSTOM", "COGNITO_USER_POOLS"

  # Request validator can be added for body and parameter validation
  # request_validator_id = aws_api_gateway_request_validator.main.id
}

# --- API Gateway Integration: POST /v1/contracts -> Lambda ---
resource "aws_api_gateway_integration" "contracts_post_lambda" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.contracts.id
  http_method = aws_api_gateway_method.contracts_post.http_method

  integration_http_method = "POST" # For Lambda proxy integration, must be POST
  type                    = "AWS_PROXY" # For Lambda proxy integration
  uri                     = aws_lambda_function.main.invoke_arn # From lambda.tf

  # Credentials for API Gateway to invoke Lambda.
  # For AWS_PROXY, this is often not needed if resource-based policy on Lambda is used.
  # However, it can be specified.
  # credentials = aws_iam_role.api_gateway_lambda_invoke_role.arn # If using a dedicated role

  # Timeout for the integration (max 29 seconds for REST APIs)
  timeout_milliseconds = 29000

  # Passthrough behavior (relevant if not using proxy integration)
  # passthrough_behavior = "WHEN_NO_MATCH"

  # Request templates (if transformations are needed, not typical for AWS_PROXY)
  # request_templates = {
  #   "application/json" = "{\n  \"body\" : $input.json('$')\n}"
  # }
}

# Lambda permission for API Gateway to invoke it
resource "aws_lambda_permission" "api_gateway_invoke_lambda" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.main.function_name # From lambda.tf
  principal     = "apigateway.amazonaws.com"

  # Source ARN to restrict to this specific API Gateway method
  source_arn = "arn:aws:execute-api:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${aws_api_gateway_rest_api.main.id}/*/${aws_api_gateway_method.contracts_post.http_method}${aws_api_gateway_resource.contracts.path}"
  # Example: "arn:aws:execute-api:us-east-1:123456789012:abcdef123/test/POST/mydemoresource"
  # Using /*/*/* for simplicity if path/stage is dynamic or for any method on the API
  # source_arn = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
  api_key_required = true # Require API Key for this method
}


# --- API Gateway Resource: /v1/contracts/{job_id} ---
resource "aws_api_gateway_resource" "contract_job" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.contracts.id
  path_part   = "{job_id}" # Path parameter
}

# --- API Gateway Method: GET /v1/contracts/{job_id} ---
resource "aws_api_gateway_method" "contract_job_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.contract_job.id
  http_method   = "GET"
  authorization = "NONE" # Authorization can be NONE if API key is the primary mechanism here.
                          # Or use AWS_IAM if requests are signed.
  api_key_required = true # Require API Key for this method

  # Define request parameters if needed (e.g., for job_id)
  request_parameters = {
    "method.request.path.job_id" = true # true means required
  }
}

# --- API Gateway Integration: GET /v1/contracts/{job_id} (Mock or Lambda) ---
# For now, using a MOCK integration as placeholder.
resource "aws_api_gateway_integration" "contract_job_get_mock" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.contract_job.id
  http_method = aws_api_gateway_method.contract_job_get.http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = "{\"statusCode\": 200}" # Minimal mock request
  }

  # Mock response
  # Note: aws_api_gateway_method_response and aws_api_gateway_integration_response
  # are needed for full mock setup. This is a simplified mock.
  # Removing MOCK integration as we are now integrating with a real Lambda
  type                    = "AWS_PROXY" # For Lambda proxy integration
  uri                     = aws_lambda_function.query_lambda.invoke_arn
  integration_http_method = "POST" # Required for AWS_PROXY to Lambda
  # Credentials not typically needed for AWS_PROXY with resource-based policy on Lambda
}

# --- API Gateway Method Response (for GET 200) ---
resource "aws_api_gateway_method_response" "job_get_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.contract_job.id
  http_method = aws_api_gateway_method.contract_job_get.http_method
  status_code = "200"
  response_models = {
    "application/json" = "Empty" # Standard practice, actual model validation by Lambda
  }
}

# Integration response is not strictly needed for AWS_PROXY if Lambda returns the exact format.
# However, it can be defined if transformations or header mappings are needed.
# For now, relying on Lambda to return the correct proxy format.
# resource "aws_api_gateway_integration_response" "job_get_lambda_response" {
#   rest_api_id = aws_api_gateway_rest_api.main.id
#   resource_id = aws_api_gateway_resource.contract_job.id
#   http_method = aws_api_gateway_method.contract_job_get.http_method
#   status_code = aws_api_gateway_method_response.job_get_200.status_code
#   # No response templates for AWS_PROXY if Lambda output is already correct
# }

# --- Lambda Permission for API Gateway to invoke Query Lambda ---
resource "aws_lambda_permission" "api_gateway_invoke_query_lambda" {
  statement_id  = "AllowAPIGatewayInvokeQueryLambda"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.query_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:aws:execute-api:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${aws_api_gateway_rest_api.main.id}/*/${aws_api_gateway_method.contract_job_get.http_method}${aws_api_gateway_resource.contract_job.path}"
}


# --- API Gateway API Key ---
resource "aws_api_gateway_api_key" "main_key" {
  name    = "${var.project_name}-main-api-key"
  enabled = true
  # value = "your-api-key-value" # Optional: If you want to set a specific key value. Otherwise, it's auto-generated.
  # customer_id = "example-customer" # Optional

  tags = {
    Name    = "${var.project_name}-main-api-key"
    Project = var.project_name
  }
}

# --- API Gateway Usage Plan ---
resource "aws_api_gateway_usage_plan" "main_plan" {
  name = "${var.project_name}-main-usage-plan"
  # description = "Main usage plan for the Contract Intelligence API"

  api_stages {
    api_id = aws_api_gateway_rest_api.main.id
    stage  = aws_api_gateway_stage.main.stage_name
  }

  throttle_settings {
    # Example: Allow 10 requests per second, with a burst of 5 requests
    rate_limit = 10
    burst_limit = 5
  }

  quota_settings {
    # Example: Allow 1000 requests per day
    limit  = 1000
    period = "DAY" # MONTH, WEEK
    # offset = 1 # Day of the month to start the quota period (e.g., 1 for first day)
  }

  tags = {
    Name    = "${var.project_name}-main-usage-plan"
    Project = var.project_name
  }
}

# --- API Gateway Usage Plan Key (Associate API Key with Usage Plan) ---
resource "aws_api_gateway_usage_plan_key" "main_plan_key" {
  key_id        = aws_api_gateway_api_key.main_key.id
  key_type      = "API_KEY" # Or "AWS_SECRET_ACCESS_KEY" for IAM-based auth on usage plan
  usage_plan_id = aws_api_gateway_usage_plan.main_plan.id
}


# --- API Gateway Deployment ---
# A new deployment is required for changes to take effect.
# The 'triggers' map can be used to force a new deployment when API resources change.
resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  # Define triggers to redeploy when API structure changes.
  # This creates a new deployment if any of these resources change.
  # Using jsonencode ensures that changes in these resources' attributes trigger redeployment.
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.contracts.id,
      aws_api_gateway_method.contracts_post.id,
      aws_api_gateway_integration.contracts_post_lambda.id,
      aws_api_gateway_resource.contract_job.id,
      aws_api_gateway_method.contract_job_get.id,
      aws_api_gateway_integration.contract_job_get_mock.id,
      # Add other resources that should trigger redeployment
    ]))
  }

  lifecycle {
    create_before_destroy = true # Avoid downtime during updates
  }

  # `stage_name` is not set here; deployment is associated with a stage separately.
}

# --- API Gateway Stage ---
resource "aws_api_gateway_stage" "main" {
  deployment_id = aws_api_gateway_deployment.main.id
  rest_api_id   = aws_api_gateway_rest_api.main.id
  stage_name    = var.api_gateway_stage_name # e.g., "dev", "v1"

  # Enable CloudWatch logging for the stage
  # Requires aws_api_gateway_account to have cloudwatch_role_arn set (in iam.tf)
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway_logs.arn
    format = jsonencode({
      requestId               = "$context.requestId"
      sourceIp                = "$context.identity.sourceIp"
      requestTime             = "$context.requestTime"
      protocol                = "$context.protocol"
      httpMethod              = "$context.httpMethod"
      resourcePath            = "$context.resourcePath"
      status                  = "$context.status"
      responseLength          = "$context.responseLength"
      errorMessage            = "$context.error.message"
      # Add more fields as needed
      # See: https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-logging.html#apigateway-cloudwatch-log-formats
    })
  }

  # Enable detailed CloudWatch metrics
  variables = {
    # Example stage variable
    # "lambdaFunctionName" = aws_lambda_function.main.function_name
  }

  # X-Ray Tracing (optional)
  # xray_tracing_enabled = true

  tags = {
    Name        = "${var.project_name}-api-stage-${var.api_gateway_stage_name}"
    Project     = var.project_name
    Environment = var.api_gateway_stage_name
  }

  depends_on = [aws_cloudwatch_log_group.api_gateway_logs] # Ensure log group exists
}

# --- CloudWatch Log Group for API Gateway ---
resource "aws_cloudwatch_log_group" "api_gateway_logs" {
  name              = "API-Gateway-Execution-Logs_${aws_api_gateway_rest_api.main.id}/${var.api_gateway_stage_name}"
  retention_in_days = 7 # Adjust as needed

  tags = {
    Name    = "${var.project_name}-api-gateway-log-group"
    Project = var.project_name
  }
}

# (Optional) API Gateway Request Validator
# resource "aws_api_gateway_request_validator" "main" {
#   name                        = "${var.project_name}-request-validator"
#   rest_api_id                 = aws_api_gateway_rest_api.main.id
#   validate_request_body       = true
#   validate_request_parameters = true # Validates query string params and headers
# }

# Note on Private API Gateway:
# For a private API, the `endpoint_configuration` block in `aws_api_gateway_rest_api`
# is set to `TYPES = ["PRIVATE"]`. This API will then only be accessible from within your VPC
# via the VPC endpoint for `execute-api` (defined as `aws_vpc_endpoint.api_gateway` in `network.tf`).
# The `policy` attribute on `aws_api_gateway_rest_api` is also crucial to ensure that
# only requests coming through the designated VPC endpoint are allowed.
# DNS for private APIs: Route 53 private hosted zones can be used to resolve API Gateway's private DNS names.
# This is often handled automatically when `private_dns_enabled = true` on the VPC endpoint.I have created `contract-intel-api/infra/variables.tf`, `contract-intel-api/infra/outputs.tf`, `contract-intel-api/infra/network.tf`, `contract-intel-api/infra/iam.tf`, `contract-intel-api/infra/s3.tf`, `contract-intel-api/infra/sqs.tf`, `contract-intel-api/infra/sagemaker.tf`, `contract-intel-api/infra/lambda.tf`, and `contract-intel-api/infra/apigateway.tf`.

Finally, I will create `contract-intel-api/infra/.gitignore` to exclude Terraform state files.
