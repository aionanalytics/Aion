# HTTPS Setup Guide for AION Analytics

This guide explains how to set up and trust the self-signed SSL certificates for local HTTPS development.

## Quick Start

1. **Generate SSL Certificates** (if not already generated):
   ```bash
   ./generate_ssl_cert.sh
   ```

2. **Start the backend with HTTPS**:
   ```bash
   python run_backend.py
   ```
   The backend will automatically use HTTPS if SSL certificates are found in the `ssl/` directory.

3. **Trust the certificate** (see platform-specific instructions below)

4. **Access the application**:
   - Backend API: `https://localhost:8000`
   - DT Backend: `https://localhost:8010`
   - Replay Service: `https://localhost:8020`

## Trusting the Self-Signed Certificate

To avoid browser warnings when accessing the HTTPS endpoints, you need to trust the self-signed certificate on your system.

### macOS

1. **Open Keychain Access**:
   - Press `Cmd + Space` and search for "Keychain Access"
   - Or navigate to `Applications > Utilities > Keychain Access`

2. **Import the certificate**:
   - Click `File > Import Items...`
   - Navigate to the AION project directory
   - Select `ssl/cert.pem` and click "Open"

3. **Trust the certificate**:
   - Find the imported certificate (search for "localhost" or "AION Analytics")
   - Double-click the certificate
   - Expand the "Trust" section
   - Set "When using this certificate" to "Always Trust"
   - Close the window (you'll be asked for your password)

4. **Restart your browser** to apply the changes

### Windows

1. **Open Certificate Manager**:
   - Press `Win + R` and type `certmgr.msc`
   - Or search for "Manage user certificates" in the Start menu

2. **Import the certificate**:
   - Right-click on `Trusted Root Certification Authorities > Certificates`
   - Select `All Tasks > Import...`
   - Click "Next" and browse to the AION project directory
   - Select `ssl/cert.pem` (you may need to change file filter to "All Files")
   - Click "Next" and ensure "Trusted Root Certification Authorities" is selected
   - Click "Next" and then "Finish"

3. **Restart your browser** to apply the changes

### Linux (Ubuntu/Debian)

1. **Copy certificate to system trust store**:
   ```bash
   sudo cp ssl/cert.pem /usr/local/share/ca-certificates/aion-analytics.crt
   ```

2. **Update certificate store**:
   ```bash
   sudo update-ca-certificates
   ```

3. **For Chrome/Chromium, you may need to import manually**:
   - Go to `chrome://settings/certificates`
   - Click "Authorities" tab
   - Click "Import"
   - Select `ssl/cert.pem`
   - Check "Trust this certificate for identifying websites"
   - Click "OK"

4. **For Firefox**:
   - Go to `about:preferences#privacy`
   - Scroll to "Certificates" and click "View Certificates"
   - Click "Authorities" tab
   - Click "Import"
   - Select `ssl/cert.pem`
   - Check "Trust this CA to identify websites"
   - Click "OK"

5. **Restart your browser** to apply the changes

### Linux (Fedora/RHEL/CentOS)

1. **Copy certificate to system trust store**:
   ```bash
   sudo cp ssl/cert.pem /etc/pki/ca-trust/source/anchors/aion-analytics.crt
   ```

2. **Update certificate store**:
   ```bash
   sudo update-ca-trust
   ```

3. **Restart your browser** to apply the changes

## Bypassing Certificate Warnings (Not Recommended)

If you don't want to trust the certificate system-wide, you can:

1. **In Chrome/Edge**: Click "Advanced" on the warning page, then "Proceed to localhost (unsafe)"
2. **In Firefox**: Click "Advanced", then "Accept the Risk and Continue"

⚠️ **Note**: You'll need to do this every time you restart the browser or clear site data.

## Environment Variables

The following environment variables control HTTPS behavior:

- `SSL_ENABLED=1` - Enable HTTPS (default: auto-detect if ssl/cert.pem exists)
- `SSL_CERT_FILE=ssl/cert.pem` - Path to SSL certificate
- `SSL_KEY_FILE=ssl/key.pem` - Path to SSL private key

You can override these in your `.env` file:

```bash
# Enable HTTPS
SSL_ENABLED=1

# Custom SSL certificate paths (optional)
SSL_CERT_FILE=/path/to/custom/cert.pem
SSL_KEY_FILE=/path/to/custom/key.pem
```

## Frontend Configuration

The frontend uses environment variables to determine backend URLs. Update your `.env.local` file in the `frontend/` directory:

```bash
# Backend URLs (use HTTPS for secure communication)
BACKEND_URL=https://localhost:8000
NEXT_PUBLIC_BACKEND_URL=https://localhost:8000

# DT Backend (use HTTPS)
DT_BACKEND_URL=https://localhost:8010
NEXT_PUBLIC_DT_BACKEND_URL=https://localhost:8010
```

## Regenerating Certificates

If your certificate expires (valid for 365 days) or you need a new one:

```bash
# Remove old certificates
rm -rf ssl/

# Generate new certificates
./generate_ssl_cert.sh
```

You'll need to trust the new certificate again following the platform-specific instructions above.

## Production Deployment

⚠️ **Important**: Self-signed certificates are for local development only.

For production deployments:

1. **Use certificates from a trusted Certificate Authority (CA)**:
   - Let's Encrypt (free): https://letsencrypt.org/
   - Commercial CAs: DigiCert, Sectigo, etc.

2. **Or use a reverse proxy** (recommended):
   - Nginx with Let's Encrypt
   - Traefik with automatic SSL
   - AWS ALB/ELB with ACM certificates
   - Cloudflare SSL

See [DEPLOYMENT.md](DEPLOYMENT.md) for production SSL setup with nginx.

## Troubleshooting

### Browser Still Shows Warning

1. **Clear browser cache and cookies**
2. **Ensure certificate is properly installed** in your system's trust store
3. **Restart the browser** completely (close all windows)
4. **Check certificate validity**:
   ```bash
   openssl x509 -in ssl/cert.pem -text -noout
   ```

### Backend Won't Start with HTTPS

1. **Check if certificate files exist**:
   ```bash
   ls -la ssl/
   ```
   Should show `cert.pem` and `key.pem`

2. **Verify certificate is valid**:
   ```bash
   openssl x509 -in ssl/cert.pem -noout -checkend 0
   ```

3. **Check file permissions**:
   ```bash
   chmod 644 ssl/cert.pem
   chmod 600 ssl/key.pem
   ```

4. **Check backend logs** for SSL-related errors:
   ```bash
   tail -f logs/aion.log
   ```

### Frontend Can't Connect to Backend

1. **Ensure backend is running on HTTPS**:
   ```bash
   curl -k https://localhost:8000/api/health
   ```

2. **Check frontend environment variables**:
   - Verify URLs use `https://` not `http://`
   - Restart Next.js dev server after changing `.env.local`

3. **Check browser console** for mixed content or SSL errors

### Certificate Verification Failed

If you get "certificate verify failed" errors:

1. **Ensure certificate is trusted** in your system (see instructions above)
2. **For development, you can disable SSL verification** (not recommended):
   ```bash
   # In Python
   export PYTHONHTTPSVERIFY=0
   
   # In Node.js
   export NODE_TLS_REJECT_UNAUTHORIZED=0
   ```

⚠️ **Never disable SSL verification in production!**

### Internal Service Communication

If backend services make internal HTTP requests to themselves (e.g., `PRIMARY_BACKEND_URL`), those requests may fail SSL verification with self-signed certificates. For local development:

1. **Option 1**: Use localhost URLs without SSL verification (Python's `requests` library accepts self-signed certificates by default for localhost)
2. **Option 2**: Set environment variable to disable SSL verification for internal calls only:
   ```bash
   export PYTHONHTTPSVERIFY=0  # Only for development
   ```

**Note**: The AION backend is designed to handle self-signed certificates for internal communication in development mode.

## Security Considerations

1. **Self-signed certificates**:
   - Only for local development
   - Do not share the private key (`ssl/key.pem`)
   - Not trusted by browsers by default

2. **Key storage**:
   - Keep `ssl/key.pem` secure (already in `.gitignore`)
   - Never commit private keys to version control
   - Set appropriate file permissions (600 for key.pem)

3. **Certificate rotation**:
   - Certificates expire after 365 days
   - Plan to regenerate before expiration
   - Test renewal process in development

4. **Production**:
   - Always use CA-signed certificates
   - Enable HSTS (HTTP Strict Transport Security)
   - Configure proper cipher suites
   - Keep certificates and keys secure

## Additional Resources

- [OpenSSL Documentation](https://www.openssl.org/docs/)
- [Let's Encrypt](https://letsencrypt.org/)
- [Mozilla SSL Configuration Generator](https://ssl-config.mozilla.org/)
- [SSL Labs Server Test](https://www.ssllabs.com/ssltest/) (for production)
