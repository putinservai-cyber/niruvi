# Appflow Document

## Project: Niruvi

### 1. User Flows

#### Flow 1: [Primary Flow Name]
```
Start → [Step 1] → [Step 2] → [Step 3] → End
```
**Decision Points:**
- [Decision]: [Path A] / [Path B]

**Error States:**
- [Error]: [Recovery action]

#### Flow 2: [Secondary Flow Name]
```
Start → [Step 1] → [Step 2] → End
```

### 2. State Management
| State | Trigger | Next State |
|-------|---------|------------|
| [State 1] | [Event] | [State 2] |
| [State 2] | [Event] | [State 3] |

### 3. Navigation Map
```
[Root]
├── [Screen A]
│   ├── [Sub-screen A1]
│   └── [Sub-screen A2]
├── [Screen B]
│   └── [Sub-screen B1]
└── [Settings]
    ├── [General]
    ├── [Advanced]
    └── [About]
```

### 4. Data Flow
```
[User Input] → [Validation] → [Processing] → [Storage/API] → [UI Update]
```

### 5. Offline Behavior
- [Feature]: [Offline capability]
- [Sync strategy]: [Description]

### 6. Deep Linking / URL Schemes
- `niruvi://[path]` → [Target screen]

### 7. Permissions Flow
| Permission | Requested At | Rationale |
|------------|--------------|-----------|
| [Permission] | [Screen/Time] | [Reason] |

### 8. Onboarding Flow
1. [Step 1]: [Description]
2. [Step 2]: [Description]
3. [Step 3]: [Description]

### 9. Error Recovery Flows
- Network error → [Retry/Offline mode]
- Auth error → [Re-login flow]
- Data corruption → [Reset/Repair]