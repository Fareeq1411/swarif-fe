# Swarif Backend API

Last updated: 11 August 2026

All endpoints accept and return JSON. Generated database IDs are created by the application and checked against all current ID-bearing tables. Clients must not provide IDs for newly created records.

## Authentication

### Login

`POST /api/auth/login`

Authenticates either an organization or a user. Passwords are checked against BCrypt hashes.

Request:

```json
{
  "email": "user@example.com",
  "password": "secret-password",
  "type": "user"
}
```

`type` must be `user` or `org`.

User login success — `200 OK`:

```json
{
  "token": "jwt-token",
  "user_id": "user-id"
}
```

Organization login success — `200 OK`:

```json
{
  "token": "jwt-token",
  "org_id": "organization-id"
}
```

Errors:

- `400 Bad Request` when required fields are missing or `type` is invalid.
- `401 Unauthorized` when the email or password is incorrect.

Note: user status/reset-password handling has not been added to login yet.

### Organization signup

`POST /api/auth/sign-up`

Creates an organization. This endpoint does not create users.

Request:

```json
{
  "name": "Example Organization",
  "email": "org@example.com",
  "password": "secret-password",
  "plan": "starter",
  "type": "org"
}
```

Required fields are `name`, `email`, and `password`. `plan` and `type` are optional. When supplied, `type` must be `org`. Passwords must contain at least 8 characters and are stored as BCrypt hashes.

Success — `201 Created`:

```json
{
  "token": "jwt-token"
}
```

Errors:

- `400 Bad Request` for invalid fields or an unsupported signup type.
- `409 Conflict` when the organization email already exists.

## Organization

### Get organization

`POST /api/organization/get-org`

Returns organization information without exposing its password hash.

Request:

```json
{
  "org_id": "organization-id"
}
```

Success — `200 OK`:

```json
{
  "id": "organization-id",
  "name": "Example Organization",
  "email": "org@example.com",
  "plan": "starter",
  "status": "ACTIVE",
  "token": 100,
  "created_time": "2026-08-07T00:00:00.000+00:00",
  "updated_time": "2026-08-07T00:00:00.000+00:00"
}
```

Errors:

- `400 Bad Request` when `org_id` is missing.
- `404 Not Found` when the organization does not exist.

### Update organization

`POST /api/organization/update`

Updates only the organization name. Email, password, plan, status, token, ID, and creation time remain unchanged.

Request:

```json
{
  "org_id": "organization-id",
  "name": "Updated Organization"
}
```

Success — `200 OK`: returns the organization using the same safe format as `get-org`.

Errors:

- `400 Bad Request` when `org_id` or `name` is missing.
- `404 Not Found` when the organization does not exist.

### Create user

`POST /api/organization/create-user`

Creates a user belonging to an existing organization.

Request:

```json
{
  "org_id": "organization-id",
  "name": "Example User",
  "email": "user@example.com",
  "password": "temporary-password",
  "msisdn": "+61400000000"
}
```

The password must contain at least 8 characters and is stored as a BCrypt hash. The insert does not provide `status`, so the database applies its default value of `INACTIVE`.

Success — `201 Created`:

```json
{
  "id": "generated-user-id",
  "org_id": "organization-id",
  "name": "Example User",
  "email": "user@example.com",
  "msisdn": "+61400000000",
  "status": "INACTIVE"
}
```

Errors:

- `400 Bad Request` for missing fields, a short password, or an invalid organization ID.
- `409 Conflict` when the user email already exists.

## User

### Get user

`POST /api/user/get-user`

Returns one user belonging to the supplied organization. Password data is never returned.

Request:

```json
{
  "org_id": "organization-id",
  "user_id": "user-id"
}
```

Success — `200 OK`:

```json
{
  "id": "user-id",
  "org_id": "organization-id",
  "name": "Example User",
  "email": "user@example.com",
  "msisdn": "+61400000000",
  "status": "ACTIVE",
  "created_time": "2026-08-07T00:00:00.000+00:00",
  "updated_time": "2026-08-07T00:00:00.000+00:00"
}
```

Errors:

- `400 Bad Request` when `org_id` or `user_id` is missing.
- `404 Not Found` when the user does not belong to the organization or does not exist.

### Update user

`POST /api/user/update`

Updates every mutable user field. `id`, `org_id`, and `created_time` cannot be changed.

Request:

```json
{
  "org_id": "organization-id",
  "user_id": "user-id",
  "name": "Updated User",
  "email": "updated@example.com",
  "password": "new-password",
  "msisdn": "+61411111111",
  "status": "ACTIVE"
}
```

Every field shown above is required. The password must contain at least 8 characters and is BCrypt-hashed before storage. `updated_time` is maintained by MySQL.

Success — `200 OK`: returns the updated user using the same safe format as `get-user`.

Errors:

- `400 Bad Request` for missing fields or a short password.
- `404 Not Found` when the user does not belong to the organization or does not exist.
- `409 Conflict` when the update violates a database constraint, such as duplicate email.

## Client application

### Send message

`POST /api/client-app/send-message`

Stores a message in the `chat` table.

Request:

```json
{
  "org_id": "organization-id",
  "user_id": "user-id",
  "type": "out",
  "message": "Hello"
}
```

`type` must be either `in` or `out`. The message ID is generated internally and `created_time` uses the database default.

Success — `201 Created`:

```json
{
  "id": "generated-chat-id",
  "org_id": "organization-id",
  "user_id": "user-id",
  "type": "out",
  "message": "Hello"
}
```

Errors:

- `400 Bad Request` for missing fields, an unsupported type, or invalid organization/user IDs.

### Get chat

`POST /api/client-app/get-chat`

Returns chat records for one user in one organization. Records are ordered newest first.

Request:

```json
{
  "org_id": "organization-id",
  "user_id": "user-id",
  "limit": 20
}
```

`limit` must be a positive integer.

Success — `200 OK`:

```json
{
  "chat": [
    {
      "id": "chat-id",
      "org_id": "organization-id",
      "user_id": "user-id",
      "type": "in",
      "message": "Hello",
      "created_time": "2026-08-07T00:00:00.000+00:00"
    }
  ]
}
```

Errors:

- `400 Bad Request` for missing IDs or an invalid limit.

### Fetch jobs

`POST /api/client-app/job/fetch-job`

Returns jobs for one user in one organization, ordered newest first.
Jobs are returned only after their `created_time` is more than 15 seconds old.

Request:

```json
{
  "org_id": "organization-id",
  "user_id": "user-id",
  "type": "active",
  "limit": 20
}
```

- `active` reads from `job_queue`.
- `inactive` reads from `job_log`.
- `limit` must be a positive integer.

Success — `200 OK`:

```json
{
  "jobs": [
    {
      "id": "job-id",
      "org_id": "organization-id",
      "user_id": "user-id",
      "title": "Reply to customer",
      "task": "{}",
      "response": "{}",
      "token": 0,
      "context_memory": "{}",
      "extra_data": "{}",
      "status": 0,
      "worker": 0,
      "created_time": "2026-08-07T00:00:00.000+00:00",
      "updated_time": "2026-08-07T00:00:00.000+00:00"
    }
  ]
}
```

`updated_time` and `extra_data` are available for active jobs. They can be absent or `null` for inactive jobs because `job_log` does not currently contain those columns.

Errors:

- `400 Bad Request` for missing fields, an unsupported type, or an invalid limit.

### Create job

`POST /api/client-app/job/create-job`

Creates a job in `job_queue`. Both `task` and `extra_data` are serialized and stored in MySQL JSON columns.

Request:

```json
{
  "org_id": "organization-id",
  "user_id": "user-id",
  "title": "Reply to customer",
  "task": {
    "action": "reply"
  },
  "extra_data": {
    "priority": 1
  }
}
```

Success — `201 Created`:

```json
{
  "id": "generated-job-id",
  "org_id": "organization-id",
  "user_id": "user-id",
  "title": "Reply to customer",
  "task": {
    "action": "reply"
  },
  "extra_data": {
    "priority": 1
  },
  "status": 0,
  "worker": 0
}
```

The database supplies defaults for status, worker, token, timestamps, response, and context memory.

Errors:

- `400 Bad Request` for missing fields, invalid JSON, or invalid organization/user IDs.

### Update job status

`POST /api/client-app/job/update-status`

Updates only the status of a job currently in `job_queue`.

Request:

```json
{
  "job_id": "job-id",
  "status": 3
}
```

Status mapping:

- `1` — success
- `2` — failed
- `3` — processing

Success — `200 OK`:

```json
{
  "job_id": "job-id",
  "status": 3,
  "status_name": "processing"
}
```

Errors:

- `400 Bad Request` when `job_id` or a valid numeric `status` is not supplied.
- `404 Not Found` when the job does not exist in `job_queue`.

### Update and archive job

`POST /api/client-app/job/update`

Updates a queued job and atomically moves it from `job_queue` to `job_log`.

Request:

```json
{
  "job_id": "job-id",
  "status": "success",
  "response": null,
  "token": 100,
  "context_memory": {
    "messages": 2
  }
}
```

Status mapping:

- `success` stores status `1`.
- `failed` stores status `2`.
- `pending` stores status `3`.

`response` may be `null`. When supplied, `response` and `context_memory` must contain valid JSON. The update, copy to `job_log`, and deletion from `job_queue` run in one database transaction.

Success — `200 OK`:

```json
{
  "job_id": "job-id",
  "status": "success",
  "status_code": 1,
  "response": null,
  "token": 100,
  "context_memory": {
    "messages": 2
  }
}
```

Errors:

- `400 Bad Request` for missing fields, invalid status, token, or JSON.
- `404 Not Found` when the job does not exist in `job_queue`.
- `409 Conflict` when the job cannot be transferred to `job_log`.

## Update history

### 11 August 2026

- Updated user login responses to include `user_id` alongside the JWT token.

### 7 August 2026

- Added `/api/organization/get-org` and a name-only `/api/organization/update` endpoint.
- Added `/api/user/get-user` and `/api/user/update` with safe user responses and BCrypt password updates.
- Added `/api/client-app/job/update` with transactional queue-to-log transfer and status mapping.
- Added `/api/client-app/send-message` for storing `in` and `out` chat messages.
- Added `/api/client-app/job/create-job`.
- Added JSON storage for `task` and `extra_data`.
- Added shared, database-aware ID generation for organizations, users, and jobs.
- Added `extra_data` to active job fetch results.

### 6 August 2026

- Added organization/user login with JWT responses.
- Added BCrypt password hashing.
- Restricted signup to organizations.
- Added organization-managed user creation with an initial `INACTIVE` status.
- Added chat retrieval with a caller-provided limit.
- Added active/inactive job retrieval from `job_queue` and `job_log`.
