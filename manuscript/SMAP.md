# SMAP — Smart Multi-Agent Platform

> Flowcharts extracted from `SMAP.drawio`. Red-highlighted nodes in the source file are rendered with red fill below (key concepts).

---

## Flowchart 1 — Top-Level Structure

```mermaid
flowchart TD
    Admin -- "Audit" --> SMAP["SMAP<br/>Smart Multi-Agent Platform"]:::key
    SMAP --> Provider
    SMAP --> Guest
    Provider --> Individual
    Provider --> Organization
    Organization --> Projects["Project (s)"]
    Individual --> APIKeys["API Keys"]
    Projects --> APIKeys
    APIKeys --> AIAgents["AI Agents"]
    AIAgents --> WorkSpace["Work Space"]
    WorkSpace --> Guest

    classDef key fill:#ffe5e5,stroke:#ff3333,color:#ff3333;
```

---

## Flowchart 2 — API Keys (Top-Level)

```mermaid
flowchart TD
    Claude --> APIKeys["API Keys"]:::key
    ChatGPT["Chat GPT"] --> APIKeys
    Gemini --> APIKeys

    APIKeys --> ProjectKey["Project Key"]
    APIKeys --> IndividualKey["Individual Key"]
    APIKeys --> KeySet["Key Set (1~N key)<br/>Settings"]

    %% Project Key → role → capability
    ProjectKey --> KU["Key Uploader"] --> KUc["Can remove the Key"]
    ProjectKey --> OO["Org Owner"] --> OOc["Can check the Usage"]
    ProjectKey --> OM["Org Member"] --> OMc["NULL"]
    ProjectKey --> PO["Project Owner"] --> POc["Can adjust the Setting"]
    ProjectKey --> PM["Project Member"] --> PMc["Can check the Usage"]

    %% Individual Key capabilities
    IndividualKey --> Carry["Can be carried into project and regarded as<br/>project key<br/>(Settings will be overwritten)"]
    Carry --> ProjectKey
    IndividualKey --> IKrm["Can remove the Key"]
    IndividualKey --> IKusage["Can check the Usage"]
    IndividualKey --> IKadj["Can adjust the Setting"]

    %% Key Set → Rotation / Limitation
    KeySet --> Rotation["Rotation Rules"]
    KeySet --> Limitation["Usage Limitation"]

    Rotation --> Group["Set multiple Keys into a group"]
    Group --> ExceedTokens["When the usage of token<br/>exceeds a certain value"]
    Group --> ErrorOccurs["If an error occurs<br/>when calling the API"]
    ExceedTokens --> AutoSwitch["Automatically switch to next key"]
    ErrorOccurs --> AutoSwitch
    ErrorOccurs --> Backoff["Can set the<br/>Exponential Backoff"]
    ErrorOccurs --> Retry["Can set the<br/>Retry Mechanism"]

    Limitation --> MaxToken["Maximum usage of token<br/>can be set per unit of time"]
    MaxToken --> Refresh["Refresh time can be set"]

    classDef key fill:#ffe5e5,stroke:#ff3333,color:#ff3333;
```

---

## Flowchart 3 — Organization, Individual, Project

```mermaid
flowchart TD
    Ind1["Individual 1"]:::key --> CreateOrg["Can create<br/>organization"]
    CreateOrg --> Org["Organization (Org)"]:::key

    Org --> OrgOwner["Org Owner"]
    Org --> OrgMember["Org Member"]

    OrgOwner --> InviteOrg["Can invite/remove Org's member"]
    InviteOrg --> Ind2["Individual 2"]:::key

    OrgOwner --> CreateProj["Can create project"]
    OrgMember --> CreateProj
    CreateProj --> Project["Project"]:::key

    Project --> ProjMember["Project Member"]
    Project --> ProjOwner["Project Owner"]

    ProjOwner --> InviteProj["Can invite/remove Project's member"]
    InviteProj --> OrgOwner
    InviteProj --> OrgMember

    classDef key fill:#ffe5e5,stroke:#ff3333,color:#ff3333;
```

---

## Flowchart 4 — AI Agent Composition

```mermaid
flowchart TD
    subgraph Providers [ ]
      direction TB
      Claude
      ChatGPT["Chat GPT"]
      Gemini
    end

    Claude --> AK["API Keys"]:::key
    ChatGPT --> AK
    Gemini --> AK

    AK --> KG["Key Groups"]
    KG --> Examples["Examples"]
    Examples --> G1["Group 1"] --> CK1a["Claude Key 1"]
    Examples --> G2["Group 2"]
    G2 --> CK1b["Claude Key 1"]
    G2 --> CK2["Claude Key 2"]
    Examples --> G3["Group 3"]
    G3 --> CK1c["Claude Key 1"]
    G3 --> GK1["Gemini Key 1"]

    KG --> SelectOne["Select one Key Group for one Agent"]
    SelectOne --> Agent["AI Agent"]:::key

    Agent --> A2A["A2A"]
    Agent --> SP["System Prompt"] --> RS["Read Strategy"]
    Agent --> RAG --> AgentDesigner["Provided by Agent Designer"]
    Agent --> MCP
    Agent --> Context

    Context --> UM["Update Mechanism"]
    Context --> General
    Context --> GraphRAG["Graph RAG"]
    GraphRAG --> GraphNote["This requires additional AI to analyze<br/>the context and then produce/update<br/>the Graph RAG"]

    classDef key fill:#ffe5e5,stroke:#ff3333,color:#ff3333;
```

---

## Flowchart 5 — AI Agent(s) → Work Space → Chat Room & Workflow

```mermaid
flowchart TD
    Agents["AI Agent (s)<br/>(1~N AI Agent)"]:::key --> WS["Work Space"]:::key

    WS --> ChatRoom["Chat Room"]
    WS --> Workflow["Workflow for multiple Agents<br/>to collaborate"]

    %% --- Chat Room ---
    ChatRoom --> GuestLink["provide a link for Guest to use"]
    ChatRoom --> ForMembers["for project members only"]
    ChatRoom --> ForOwners["for project owners only"]
    ChatRoom --> Input["Can Input:<br/>text, audio, photo and archive"]
    ChatRoom --> Display["Allowed to be displayed:<br/>MarkDown Syntax (for text),<br/>Embedded photo (via public link)"]

    %% --- Workflow ---
    Workflow --> WakeUp["When will Agent wake up?"]
    Workflow --> ChangeSetting["Can Agent change their own<br/>setting of wake up?"]
    Workflow --> Approval["Does the action require approval<br/>from other Agent before it can proceed?<br/>And from whom?"]
    Workflow --> Instruct["Is it permissible to instruct other Agent?<br/>If so, which Agent can be instructed?"]
    Workflow --> SubAgent["Is it allowed to create sub-agent?<br/>What is the maximum number that<br/>can be created?"]

    WakeUp --> NMsg["Every N messages received from users"]
    WakeUp --> Silence["Silence in the chat room<br/>for more than T minutes"]
    WakeUp --> CallOnly["Only allowed when receiving<br/>a call from another Agent"]
    Silence --> AutoStop["After N rounds without any user response,<br/>the loop will automatically stop."]

    ChangeSetting --> WakeUp
    ChangeSetting --> RefreshT["Will these settings refresh<br/>automatically after T hours?"]

    classDef key fill:#ffe5e5,stroke:#ff3333,color:#ff3333;
```

---

## Key Concepts Index

Red-highlighted nodes in the original diagram:

| # | Node | Flowchart |
|---|------|-----------|
| 1 | SMAP — Smart Multi-Agent Platform | 1 |
| 2 | API Keys (platform-level) | 2 |
| 3 | Organization (Org) | 3 |
| 4 | Individual 1 / Individual 2 | 3 |
| 5 | Project | 3 |
| 6 | API Keys (agent-scoped) | 4 |
| 7 | AI Agent | 4 |
| 8 | AI Agent (s) (1~N AI Agent) | 5 |
| 9 | Work Space | 5 |
