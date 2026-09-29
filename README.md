# FSx ONTAP Local Group Manager

A small FastAPI application with a browser UI for managing **CIFS local groups** on Amazon FSx for NetApp ONTAP.

## Features

- List ONTAP SVMs
- List CIFS local groups for an SVM
- Create local groups
- Read group details
- Rename a group
- Update description
- Delete a group
- List group members
- Add a local user, AD user, or AD group to a local group
- Remove a member
- Swagger/OpenAPI documentation at `/docs`
- Docker / Docker Compose
- ONTAP credentials supplied through environment variables

## Architecture

```text
Browser
   |
   | HTTP
   v
FastAPI + HTML/JS
   |
   | HTTPS + ONTAP REST API
   v
FSx for ONTAP management endpoint
   |
   v
SVM -> CIFS Local Groups
```

## Important FSx/ONTAP networking requirement

The container must be able to reach the **ONTAP management endpoint/LIF** over HTTPS (normally TCP 443).

For an AWS deployment, put the container on a network that can route to the FSx for ONTAP management endpoint and allow TCP 443 in the relevant security/network controls.

Do not expose the application directly to the Internet without adding authentication and HTTPS.

## 1. Configure credentials

```bash
cp .env.example .env
vi .env
```

Example:

```dotenv
ONTAP_HOST=fsx-svm-management.example.com
ONTAP_USERNAME=fsxadmin
ONTAP_PASSWORD=your-password
ONTAP_VERIFY_SSL=false
DEFAULT_SVM=fsx-windows-new-test
```

For production, prefer a dedicated ONTAP account with only the privileges needed by this application rather than using `fsxadmin`.

If ONTAP has a trusted certificate, set:

```dotenv
ONTAP_VERIFY_SSL=true
```

## 2. Start with Docker

```bash
docker compose up -d --build
```

Check:

```bash
docker compose ps
docker compose logs -f
```

Open:

```text
http://<server-ip>:8000
```

Swagger:

```text
http://<server-ip>:8000/docs
```

## 3. Test connectivity from the container

```bash
docker exec -it fsx-local-group-manager python -c \
'import urllib.request; print(urllib.request.urlopen("https://YOUR-ONTAP-HOST/api/cluster", timeout=10).status)'
```

If `ONTAP_VERIFY_SSL=false`, the Python URL test above can fail because it verifies the certificate. Use curl with `-k` or test through the application instead.

## API examples

### List SVMs

```bash
curl http://localhost:8000/api/svms
```

### List groups

```bash
curl "http://localhost:8000/api/groups?svm=fsx-windows-new-test"
```

### Create a group

```bash
curl -X POST \
  "http://localhost:8000/api/groups?svm=fsx-windows-new-test" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "fsx_readonly",
    "description": "Read-only FSx access"
  }'
```

### Get a group

Replace the SID with the SID returned by the list/create operation.

```bash
curl \
  "http://localhost:8000/api/groups/S-1-5-21-...?svm=fsx-windows-new-test"
```

Correct shell form:

```bash
curl \
  "http://localhost:8000/api/groups/S-1-5-21-...?svm=fsx-windows-new-test"
```

### Update a group

```bash
curl -X PATCH \
  "http://localhost:8000/api/groups/S-1-5-21-...?svm=fsx-windows-new-test" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated description"
  }'
```

### Add an AD user

```bash
curl -X POST \
  "http://localhost:8000/api/groups/S-1-5-21-.../members?svm=fsx-windows-new-test" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CAPE\\murali"
  }'
```

### Add an AD group

```bash
curl -X POST \
  "http://localhost:8000/api/groups/S-1-5-21-.../members?svm=fsx-windows-new-test" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CAPE\\cape-full"
  }'
```

### List members

```bash
curl \
  "http://localhost:8000/api/groups/S-1-5-21-.../members?svm=fsx-windows-new-test"
```

### Remove a member

```bash
curl -X DELETE \
  "http://localhost:8000/api/groups/S-1-5-21-.../members/CAPE%5Cmurali?svm=fsx-windows-new-test"
```

### Delete group

```bash
curl -X DELETE \
  "http://localhost:8000/api/groups/S-1-5-21-...?svm=fsx-windows-new-test"
```

## ONTAP REST API mapping

This project uses these ONTAP endpoints:

```text
GET    /api/svm/svms

GET    /api/protocols/cifs/local-groups
POST   /api/protocols/cifs/local-groups

GET    /api/protocols/cifs/local-groups/{svm.uuid}/{sid}
PATCH  /api/protocols/cifs/local-groups/{svm.uuid}/{sid}
DELETE /api/protocols/cifs/local-groups/{svm.uuid}/{sid}

GET    /api/protocols/cifs/local-groups/{svm.uuid}/{sid}/members
POST   /api/protocols/cifs/local-groups/{svm.uuid}/{sid}/members
DELETE /api/protocols/cifs/local-groups/{svm.uuid}/{sid}/members
```

ONTAP generates the group SID when the group is created. The application therefore uses the group name for creation but the SID for subsequent update/delete/member operations.

## Security notes

This project is intentionally a lab/admin application. Before production use:

1. Add user authentication and authorization to the FastAPI application.
2. Put it behind HTTPS.
3. Do not expose port 8000 publicly.
4. Use a dedicated ONTAP service account.
5. Store credentials in AWS Secrets Manager, not `.env`.
6. Enable ONTAP TLS verification.
7. Add audit logging for create/update/delete/member changes.
8. Consider CSRF protection if using cookie-based authentication.
9. Restrict application access with security groups/NACLs.
10. Consider an allow-list of SVMs if the service should manage only one SVM.

## AWS Secrets Manager next step

For an ECS deployment, the recommended pattern is:

```text
AWS Secrets Manager
       |
       | ECS secret injection
       v
FastAPI container
       |
       | HTTPS 443
       v
FSx ONTAP SVM management LIF
```

Do not bake the ONTAP password into the Docker image.

## Troubleshooting

### 401 / 403

The ONTAP account authenticated but does not have the required permissions. Check the ONTAP user/application privileges.

### 404 SVM

Verify:

```bash
GET /api/svm/svms
```

and use the exact SVM name.

### Connection timeout

Check:

- Container subnet routing
- FSx ONTAP management endpoint reachability
- Security groups
- NACLs
- DNS resolution
- TCP 443

### SSL certificate error

For a lab only:

```dotenv
ONTAP_VERIFY_SSL=false
```

For production, install/use a trusted certificate and set it to `true`.

## Scope

This application manages **CIFS local groups**. It does not create or modify Active Directory groups. AD users/groups can be members of an ONTAP local group where ONTAP can resolve them.

It also does not change NTFS ACLs on volumes. Group management and filesystem/share authorization are separate layers.
