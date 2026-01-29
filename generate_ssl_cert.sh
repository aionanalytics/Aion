#!/bin/bash
#
# Generate self-signed SSL certificate for local development
# This script creates SSL certificates in the ssl/ directory
#

set -e

echo "🔐 Generating self-signed SSL certificate for AION Analytics..."

# Create ssl directory if it doesn't exist
mkdir -p ssl

# Generate private key and certificate
openssl req -x509 -newkey rsa:4096 -nodes \
  -keyout ssl/key.pem \
  -out ssl/cert.pem \
  -days 365 \
  -subj "/C=US/ST=State/L=City/O=AION Analytics/OU=Development/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,DNS:127.0.0.1,IP:127.0.0.1,IP:0.0.0.0"

echo "✅ SSL certificate generated successfully!"
echo ""
echo "📁 Certificate files:"
echo "   - Private key: ssl/key.pem"
echo "   - Certificate: ssl/cert.pem"
echo ""
echo "⚠️  This is a self-signed certificate for local development only."
echo "   For production, use certificates from a trusted Certificate Authority."
echo ""
echo "📖 To trust this certificate and avoid browser warnings:"
echo "   See HTTPS_SETUP.md for platform-specific instructions."
