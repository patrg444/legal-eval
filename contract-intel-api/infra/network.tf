# --- VPC ---
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr_block
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.project_name}-vpc"
    Project     = var.project_name
    Environment = "dev" # Or a variable
  }
}

# --- Internet Gateway ---
resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name    = "${var.project_name}-igw"
    Project = var.project_name
  }
}

# --- Subnets ---
# Public Subnets
resource "aws_subnet" "public" {
  count             = length(var.public_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index % length(var.availability_zones)] # Distribute across AZs
  map_public_ip_on_launch = true

  tags = {
    Name    = "${var.project_name}-public-subnet-${count.index + 1}"
    Project = var.project_name
    Tier    = "Public"
  }
}

# Private Subnets
resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index % length(var.availability_zones)] # Distribute across AZs

  tags = {
    Name    = "${var.project_name}-private-subnet-${count.index + 1}"
    Project = var.project_name
    Tier    = "Private"
  }
}

# --- Elastic IPs for NAT Gateways ---
resource "aws_eip" "nat" {
  count = length(var.public_subnet_cidrs) # One NAT Gateway per public subnet/AZ for HA
  domain   = "vpc" # Changed from 'vpc = true' to 'domain = "vpc"' for newer AWS provider versions

  tags = {
    Name    = "${var.project_name}-nat-eip-${count.index + 1}"
    Project = var.project_name
  }
}

# --- NAT Gateways ---
resource "aws_nat_gateway" "nat" {
  count         = length(var.public_subnet_cidrs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name    = "${var.project_name}-nat-gw-${count.index + 1}"
    Project = var.project_name
  }

  depends_on = [aws_internet_gateway.gw]
}

# --- Route Tables ---
# Public Route Table
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }

  tags = {
    Name    = "${var.project_name}-public-rt"
    Project = var.project_name
  }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private Route Tables (one per NAT Gateway for zonal independence)
resource "aws_route_table" "private" {
  count  = length(aws_subnet.private) # Typically one route table per private subnet or per AZ for private subnets
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat[count.index % length(aws_nat_gateway.nat)].id # Route to NAT in the same AZ
  }

  tags = {
    Name    = "${var.project_name}-private-rt-${count.index + 1}"
    Project = var.project_name
  }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# --- VPC Endpoints ---
# S3 Gateway Endpoint
resource "aws_vpc_endpoint" "s3" {
  vpc_id       = aws_vpc.main.id
  service_name = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"

  # Associate with all route tables in the VPC (both public and private)
  # For more granular control, specify specific route table IDs.
  route_table_ids = concat(
    [aws_route_table.public.id],
    aws_route_table.private[*].id
  )

  tags = {
    Name    = "${var.project_name}-s3-vpce"
    Project = var.project_name
  }
}

# Security Group for Interface Endpoints
resource "aws_security_group" "interface_endpoints_sg" {
  name        = "${var.project_name}-interface-endpoints-sg"
  description = "Security group for VPC interface endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443 # HTTPS
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr_block] # Allow traffic from within the VPC
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # Allow all outbound traffic
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-interface-endpoints-sg"
    Project = var.project_name
  }
}

# SageMaker Runtime Interface Endpoint
resource "aws_vpc_endpoint" "sagemaker_runtime" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.sagemaker.runtime"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids = aws_subnet.private[*].id # Deploy endpoint network interfaces in private subnets
  security_group_ids = [aws_security_group.interface_endpoints_sg.id]

  tags = {
    Name    = "${var.project_name}-sagemaker-runtime-vpce"
    Project = var.project_name
  }
}

# API Gateway Interface Endpoint (for private APIs)
resource "aws_vpc_endpoint" "api_gateway" {
  count = 1 # Make this conditional if a private API is not always used.
            # For this project, it's specified for PrivateLink.

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.execute-api"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids = aws_subnet.private[*].id # Deploy endpoint network interfaces in private subnets
  security_group_ids = [aws_security_group.interface_endpoints_sg.id]

  tags = {
    Name    = "${var.project_name}-apigw-vpce"
    Project = var.project_name
  }
}

# CloudWatch Logs Interface Endpoint (Good practice for VPC-locked resources)
resource "aws_vpc_endpoint" "logs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.logs"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids         = aws_subnet.private[*].id
  security_group_ids = [aws_security_group.interface_endpoints_sg.id]

  tags = {
    Name    = "${var.project_name}-logs-vpce"
    Project = var.project_name
  }
}

# Textract Interface Endpoint (If Lambda needs to call Textract via PrivateLink)
resource "aws_vpc_endpoint" "textract" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.textract"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids         = aws_subnet.private[*].id
  security_group_ids = [aws_security_group.interface_endpoints_sg.id]

  tags = {
    Name    = "${var.project_name}-textract-vpce"
    Project = var.project_name
  }
}

# SQS Interface Endpoint (If Lambda needs to call SQS via PrivateLink)
resource "aws_vpc_endpoint" "sqs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.sqs"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids         = aws_subnet.private[*].id
  security_group_ids = [aws_security_group.interface_endpoints_sg.id]

  tags = {
    Name    = "${var.project_name}-sqs-vpce"
    Project = var.project_name
  }
}
