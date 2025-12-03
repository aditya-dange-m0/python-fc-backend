# 📋 **ASSET UPLOAD FLOW - SIMPLE LOGIC FOR CURSOR**

## **HIGH-LEVEL ARCHITECTURE**

```
User uploads file → Frontend → NestJS Backend → S3 Storage → Processor (Image/Document)
→ MongoDB (embeddings + metadata) → Session Context (AI-ready summaries)
```

---

## **STEP-BY-STEP FLOW LOGIC**

### **STEP 1: Frontend Upload**

- User selects file in browser (PDF, image, .py, .txt, etc.)
- Frontend sends file + `session_id` + `user_id` to NestJS `/api/assets/upload` endpoint
- Uses FormData with multipart/form-data

### **STEP 2: NestJS Receives File**

- Controller validates: file exists, session_id exists, user_id exists
- Checks file size and type (security)
- Passes to `AssetsService`

### **STEP 3: S3 Upload**

- NestJS uploads file to DigitalOcean Spaces (S3-compatible storage)
- Generates unique filename: `uploads/{user_id}/{timestamp}-{filename}`
- Returns S3 URL: `https://full-stack.blr1.digitaloceanspaces.com/uploads/...`

### **STEP 4: Route to Processor**

- NestJS checks file extension and mimetype
- **If image** (.jpg, .png, .gif, .svg) → Route to `ImageProcessorService`
- **If document/code** (.pdf, .py, .js, .txt, .md, .docx) → Route to `DocumentProcessorService`
- Both processors are called with: `s3_url`, `filename`, `session_id`, `user_id`

### **STEP 5A: Image Processing** (if image)

- Python `image_processor.py` downloads image from S3 URL
- Generates visual description using vision model (GPT-4-Vision / Claude)
- Stores embedding in MongoDB `image_embeddings` collection
- Returns: `{success: true, summary: "description", image_url: "..."}`

### **STEP 5B: Document Processing** (if document/code)

- Python `document_processor_v1.py` downloads document from S3 URL
- Loads content (PDF → PyMuPDF, code → text loader, DOCX → Docling)
- Generates summary using LLM (Grok-4-fast via OpenRouter)
- Chunks document into smaller pieces (1000-1500 tokens per chunk)
- Creates embeddings for each chunk (text-embedding-3-small)
- Stores chunks in MongoDB `document_embeddings` collection with metadata:
  - `session_id` (for filtering)
  - `user_id`
  - `filename`
  - `file_type` (.py, .pdf, etc.)
  - `is_code_file` (true/false)
  - `language` (python, javascript, etc.)
  - `chunk_index` (0, 1, 2...)
  - `total_chunks` (15)
- Returns: `{success: true, summary: "...", total_chunks: 15, token_count: 5000}`

### **STEP 6: Link to Session Context**

- NestJS `SessionContextService` finds or creates session document in MongoDB
- Adds asset metadata to session:
  ```
  {
    sessionId: "session_123",
    userId: "user_456",
    assets: [
      {
        assetId: "asset_789",
        filename: "script.py",
        assetType: "code",
        s3Url: "https://...",
        summary: "This Python script implements...",
        addedAt: "2025-12-03T10:30:00Z"
      }
    ],
    aiContext: {
      assetSummaries: [
        "script.py (code): This Python script implements file operations..."
      ],
      totalAssets: 1
    }
  }
  ```

### **STEP 7: AI Context Storage**

- Summary is stored in `aiContext.assetSummaries` array
- This context is retrieved when LangGraph agent needs document awareness
- Agent can say: "I see you uploaded script.py which implements file operations. Let me help you with that."

### **STEP 8: Return Response**

- NestJS returns to frontend:
  ```json
  {
    "success": true,
    "data": {
      "asset_id": "asset_789",
      "filename": "script.py",
      "asset_type": "code",
      "s3_url": "https://...",
      "session_id": "session_123",
      "summary": "This Python script implements...",
      "chunks": 8,
      "token_count": 6709
    }
  }
  ```

---

## **KEY ARCHITECTURAL DECISIONS**

### **1. Why S3 First?**

- Files stored permanently (even if processing fails)
- Python processors don't need direct file access from NestJS
- Scalable (processors can be on different servers)
- S3 URLs work everywhere (download from anywhere)

### **2. Why Python Processors?**

- LangChain, LangGraph, embedding models are Python ecosystems
- Better ML/AI library support
- NestJS focuses on API/routing, Python focuses on ML processing

### **3. Why Session-Based Storage?**

- Each user conversation = one session
- All assets uploaded in that conversation belong to that session
- Search only returns documents from current session (privacy + relevance)
- AI agent only sees context from current session

### **4. Why Unified Document Collection?**

- Code files (.py, .js) are just text documents with syntax
- Storing code separately creates duplicate infrastructure
- Single vector search covers all text content (docs + code)
- Filter by `is_code_file` metadata if needed

### **5. Why Chunk Documents?**

- Large documents (50 pages PDF) can't fit in LLM context window
- Embedding models have 8191 token limit
- Chunking allows semantic search (find relevant section, not whole doc)
- Example: Search "authentication function" → returns chunk with auth code, not entire 10,000-line file

---

## **DATA FLOW DIAGRAM**

```
┌──────────┐
│ Frontend │
└────┬─────┘
     │ file + session_id + user_id
     ▼
┌──────────────────────────────────────┐
│          NestJS Backend              │
│ ┌──────────────────────────────────┐ │
│ │ 1. Upload Controller             │ │
│ │    - Validate                    │ │
│ │    - Check file type             │ │
│ └───────────┬──────────────────────┘ │
│             ▼                         │
│ ┌──────────────────────────────────┐ │
│ │ 2. S3 Service                    │ │
│ │    - Upload to Spaces            │ │
│ │    - Return URL                  │ │
│ └───────────┬──────────────────────┘ │
│             ▼                         │
│ ┌──────────────────────────────────┐ │
│ │ 3. Asset Router                  │ │
│ │    if image → ImageProcessor     │ │
│ │    if doc/code → DocumentProcessor│ │
│ └─────┬──────────────┬─────────────┘ │
│       │              │                │
│   ┌───▼────┐    ┌───▼────────┐      │
│   │ Image  │    │ Document   │      │
│   │ Proc   │    │ Processor  │      │
│   └───┬────┘    └───┬────────┘      │
│       │             │                 │
└───────┼─────────────┼─────────────────┘
        │             │
        │             │ Call Python
        ▼             ▼
┌────────────────────────────────────┐
│     Python Processors              │
│  - Download from S3 URL            │
│  - Process (load, chunk, embed)    │
│  - Generate summary (LLM)          │
│  - Store embeddings in MongoDB     │
└────────────┬───────────────────────┘
             │
             ▼
┌────────────────────────────────────┐
│     MongoDB Collections            │
│  - document_embeddings             │
│    (all chunks with session_id)    │
│  - image_embeddings                │
│  - session_context                 │
│    (summaries for AI)              │
└────────────────────────────────────┘
```

---

## **MONGODB STRUCTURE**

### **document_embeddings collection:**

```json
{
  "_id": "ObjectId(...)",
  "page_content": "def process_file(path): ...",
  "embedding": [0.123, -0.456, ...],  // 1536 dimensions
  "metadata": {
    "session_id": "session_123",
    "user_id": "user_456",
    "filename": "script.py",
    "file_type": "py",
    "is_code_file": true,
    "language": "python",
    "chunk_index": 0,
    "total_chunks": 8,
    "s3_url": "https://..."
  }
}
```

### **session_context collection:**

```json
{
  "_id": "ObjectId(...)",
  "sessionId": "session_123",
  "userId": "user_456",
  "assets": [
    {
      "assetId": "asset_789",
      "filename": "script.py",
      "assetType": "code",
      "summary": "File operations script...",
      "addedAt": "2025-12-03T10:30:00Z"
    }
  ],
  "aiContext": {
    "assetSummaries": [
      "script.py (code): File operations script with E2B sandbox integration"
    ],
    "totalAssets": 1
  }
}
```

---

## **HOW AI AGENT USES THIS**

1. User asks: "What files did I upload?"

   - Agent queries `session_context` by `sessionId`
   - Returns: "You uploaded script.py (Python code for file operations)"

2. User asks: "Show me the authentication function"

   - Agent does vector search on `document_embeddings` with filter: `{session_id: "session_123"}`
   - Returns top 3 relevant chunks
   - Agent reads chunks and responds with code

3. User asks: "Create a new file based on my uploaded code"
   - Agent retrieves summary from `aiContext`
   - Uses summary as context: "User has script.py with file operations"
   - Generates new code influenced by existing patterns

---

## **BENEFITS**

✅ **Separation of Concerns**: NestJS = routing, Python = AI processing  
✅ **Scalability**: Processors can run on separate machines  
✅ **Session Isolation**: Each conversation has its own asset context  
✅ **Semantic Search**: Find relevant chunks, not just exact text matches  
✅ **AI-Ready**: Summaries stored for quick context injection  
✅ **No Redundancy**: Code files use document processor (unified storage)  
✅ **Persistent Storage**: S3 URLs work even if services restart

This architecture is production-ready and follows modern microservices patterns! 🚀

[1](https://www.aprimo.com/blog/what-is-a-digital-asset-management-architecture)
[2](https://www.jatit.org/volumes/Vol103No2/4Vol103No2.pdf)
[3](https://www.hyland.com/en/resources/articles/dam-process-flow)
[4](https://www.sharedien.com/en/post/digital-asset-management)
[5](https://www.sprinklr.com/blog/digital-asset-management/)
[6](https://en.wikipedia.org/wiki/Digital_asset_management)
[7](https://daminion.net/articles/tips/top-5-dam-best-practices-guide-2022/)
[8](https://experienceleague.adobe.com/en/docs/experience-manager-cloud-service/content/assets/overview)
[9](https://www.opentext.com/media/product-overview/opentext-digital-asset-management-po-en.pdf)

================================================================================
FINALIZED ARCHITECTURE - SEPARATION OF CONCERNS (2025-01-03)
================================================================================

## **CORE PRINCIPLE: CLEAR SEPARATION OF RESPONSIBILITIES**

**Python Backend (Processing Layer):**

- ✅ Processes files (document/image analysis, RAG embeddings)
- ✅ Stores embeddings in MongoDB (for RAG search)
- ✅ Returns structured results
- ❌ NO database operations for metadata storage
- ❌ NO session management

**NestJS Backend (Business Logic Layer):**

- ✅ File upload to S3
- ✅ Metadata storage in Project.metadata (PostgreSQL)
- ✅ Session/project management
- ✅ Retrieves asset context for chat requests

---

## **REFINED FLOW**

### **STEP 1: File Upload (NestJS)**

- Frontend sends file to NestJS `/api/assets/upload`
- NestJS validates, uploads to S3, gets S3 URL
- NestJS determines file type (image vs document)

### **STEP 2: Route to Python Processor (NestJS)**

- **If image** → NestJS calls Python `/assets/process-image`
- **If document** → NestJS calls Python `/assets/process-document`

### **STEP 3: Python Processing**

- **Document Processing:**

  - Downloads from S3 URL
  - Loads content (PDF, code, DOCX, etc.)
  - Generates summary using LLM
  - Chunks document intelligently
  - Creates embeddings for each chunk
  - Stores embeddings in MongoDB `document_embeddings` collection
  - Returns `DocumentProcessingResult`

- **Image Processing:**
  - Downloads from S3 URL
  - Analyzes with vision model (Grok-4-fast)
  - Generates YAML-formatted analysis
  - Stores embedding in MongoDB `image_embeddings` collection
  - Returns `ImageProcessingResult`

### **STEP 4: Metadata Storage (NestJS)**

- NestJS receives processing result from Python
- NestJS stores in `Project.metadata` (PostgreSQL JSONB):
  - Documents → `Project.metadata.documents[]`
  - Images → `Project.metadata.images[]`
- Updates `Project.metadata.aiContext` with summaries

### **STEP 5: Chat Request with Asset Context**

- When user sends chat message:
  - NestJS retrieves `Project.metadata` for session
  - Extracts image URLs and document summaries
  - Passes to Python `/chat` endpoint with:
    - `image_urls`: Array of image URLs (if images uploaded)
    - `document_context`: Array of `{filename, type, summary}` (if documents uploaded)
- Python agent receives context and uses:
  - Image URLs for vision model input
  - Document summaries for context awareness
  - RAG search for detailed document retrieval

---

## **FINALIZED METADATA STRUCTURE**

### **Project.metadata (PostgreSQL JSONB)**

```json
{
  "documents": [
    {
      "asset_id": "uuid-string",
      "filename": "script.py",
      "s3_url": "https://full-stack.blr1.digitaloceanspaces.com/uploads/...",
      "file_type": "py",
      "language": "python",
      "is_code_file": true,
      "summary": "This Python script implements file operations with E2B sandbox integration...",
      "total_chunks": 8,
      "token_count": 6709,
      "rag_processed": true,
      "added_at": "2025-01-03T10:30:00Z"
    },
    {
      "asset_id": "uuid-string",
      "filename": "architecture.pdf",
      "s3_url": "https://full-stack.blr1.digitaloceanspaces.com/uploads/...",
      "file_type": "pdf",
      "language": null,
      "is_code_file": false,
      "summary": "System architecture document describing microservices pattern...",
      "total_chunks": 15,
      "token_count": 12500,
      "rag_processed": true,
      "added_at": "2025-01-03T11:15:00Z"
    }
  ],
  "images": [
    {
      "asset_id": "uuid-string",
      "filename": "dashboard.png",
      "s3_url": "https://full-stack.blr1.digitaloceanspaces.com/uploads/...",
      "image_url": "https://full-stack.blr1.digitaloceanspaces.com/uploads/...",
      "analysis": "Image Type: design\nPurpose: dashboard\nLayout: navbar+cards\nColors: primary:#1e40af...",
      "rag_processed": true,
      "added_at": "2025-01-03T10:45:00Z"
    }
  ],
  "aiContext": {
    "documentSummaries": [
      "script.py (python): This Python script implements file operations with E2B sandbox integration...",
      "architecture.pdf (pdf): System architecture document describing microservices pattern..."
    ],
    "imageSummaries": [
      "dashboard.png: Modern dashboard design with navbar and card layout..."
    ],
    "totalDocuments": 2,
    "totalImages": 1,
    "totalAssets": 3
  }
}
```

---

## **PYTHON ENDPOINTS**

### **POST `/assets/process-document`**

**Request:**

```json
{
  "s3_url": "https://...",
  "filename": "script.py",
  "session_id": "project_123",
  "user_id": "user_456",
  "content_type": "text/x-python" // optional
}
```

**Response:**

```json
{
  "success": true,
  "asset_id": "uuid-string",
  "filename": "script.py",
  "s3_url": "https://...",
  "file_type": "py",
  "language": "python",
  "is_code_file": true,
  "summary": "This Python script implements...",
  "total_chunks": 8,
  "token_count": 6709,
  "rag_processed": true,
  "message": "Document processed successfully: script.py"
}
```

### **POST `/assets/process-image`**

**Request:**

```json
{
  "s3_url": "https://...",
  "filename": "dashboard.png",
  "session_id": "project_123",
  "content_type": "image/png" // optional
}
```

**Response:**

```json
{
  "success": true,
  "asset_id": "uuid-string",
  "filename": "dashboard.png",
  "s3_url": "https://...",
  "image_url": "https://...",
  "analysis": "Image Type: design\nPurpose: dashboard...",
  "rag_processed": true,
  "message": "Image processed successfully: dashboard.png"
}
```

---

## **CHAT REQUEST WITH ASSET CONTEXT**

### **Enhanced Chat Request (NestJS → Python)**

```json
{
  "message": "Create a dashboard based on my uploaded image",
  "session_id": "project_123",
  "user_id": "user_456",
  "image_urls": [
    "https://full-stack.blr1.digitaloceanspaces.com/uploads/.../dashboard.png"
  ],
  "document_context": [
    {
      "filename": "script.py",
      "type": "py",
      "summary": "This Python script implements file operations..."
    }
  ]
}
```

### **Chat Endpoint Flow (Python)**

1. Receives chat request with asset context
2. If `image_urls` present → Include in vision model message
3. If `document_context` present → Include summaries in system prompt
4. Agent can use RAG search with `session_id` filter for detailed retrieval
5. Responds with context-aware output

---

## **SESSION SEPARATION OF CONCERNS**

### **Session = Project (One-to-One Mapping)**

- `session_id` = `project.id` (Project primary key)
- Each session has isolated asset context
- Assets are filtered by `session_id` in:
  - MongoDB embeddings (RAG search)
  - PostgreSQL metadata (Project.metadata)

### **Asset Isolation**

- Documents: Filtered by `session_id` in MongoDB `document_embeddings` metadata
- Images: Filtered by `session_id` in MongoDB `image_embeddings` metadata
- Metadata: Stored per-project in `Project.metadata`

### **Privacy & Relevance**

- ✅ User A's session cannot see User B's assets
- ✅ Session A's assets don't appear in Session B's context
- ✅ RAG search only returns chunks from current session
- ✅ LLM context only includes assets from current session

---

## **KEY ARCHITECTURAL DECISIONS**

### **1. Why Separate Images and Documents in Metadata?**

- **Clear Organization**: Easy to query/filter by type
- **Type-Specific Operations**: Different handling for images vs documents
- **Performance**: Smaller arrays for faster queries
- **Clarity**: Explicit structure vs mixed array

### **2. Why Python Doesn't Store Metadata?**

- **Single Source of Truth**: NestJS manages all business data
- **Consistency**: All project metadata in one place (PostgreSQL)
- **Separation of Concerns**: Python = processing, NestJS = data management
- **Simplicity**: Python focuses on ML/AI tasks only

### **3. Why Metadata in Project, Not Separate Collection?**

- **Co-location**: Assets belong to projects, store together
- **Atomic Updates**: Update project + assets in single transaction
- **Performance**: No joins needed for asset context
- **Simplicity**: One query gets everything

### **4. Why Image URLs in Chat Request?**

- **Real-time Context**: Agent sees images during conversation
- **Vision Models**: Direct URL input for GPT-4-Vision/Claude
- **Flexibility**: Can include/exclude images per request
- **Efficiency**: No need to query metadata on every chat

### **5. Why Document Summaries in Chat Request?**

- **Context Awareness**: Agent knows what user uploaded
- **Proactive Help**: Agent can reference documents without search
- **Efficiency**: Pre-computed summaries, no need to query metadata
- **Conversational**: "I see you uploaded script.py which does X..."

---

## **NESTJS RESPONSIBILITIES**

### **Asset Upload Service**

1. Receive file upload from frontend
2. Upload to S3, get S3 URL
3. Determine file type (image/document)
4. Call Python processor endpoint
5. Store result in `Project.metadata.documents[]` or `Project.metadata.images[]`
6. Update `Project.metadata.aiContext`
7. Return success response to frontend

### **Chat Service (Before Calling Python)**

1. Retrieve `Project.metadata` for session
2. Extract:
   - `image_urls`: All image URLs from `Project.metadata.images[]`
   - `document_context`: Array of `{filename, type, summary}` from `Project.metadata.documents[]`
3. Include in chat request to Python `/chat` endpoint

---

## **BENEFITS OF FINALIZED ARCHITECTURE**

✅ **Clear Separation**: Python = ML processing, NestJS = business logic  
✅ **Single Source of Truth**: All metadata in PostgreSQL  
✅ **Type Safety**: Separate arrays for images/documents  
✅ **Session Isolation**: Complete privacy between sessions  
✅ **Efficient Context**: Pre-computed summaries in chat requests  
✅ **Scalability**: Processors can scale independently  
✅ **Maintainability**: Each service has clear responsibilities  
✅ **Performance**: No redundant queries or joins

---

## **IMPLEMENTATION CHECKLIST**

### **Python Backend**

- [x] `/assets/process-document` endpoint
- [x] `/assets/process-image` endpoint
- [x] Document processor stores embeddings with `session_id`
- [x] Image processor stores embeddings with `session_id`
- [x] Return structured results (no DB metadata operations)

### **NestJS Backend**

- [ ] Store document results in `Project.metadata.documents[]`
- [ ] Store image results in `Project.metadata.images[]`
- [ ] Update `Project.metadata.aiContext` on asset upload
- [ ] Extract asset context before chat requests
- [ ] Include `image_urls` and `document_context` in chat payload

### **Chat Integration**

- [x] Python `/chat` endpoint accepts `image_urls` array
- [x] Python `/chat` endpoint accepts `document_context` array
- [x] Agent uses image URLs for vision input (multimodal message support)
- [x] Agent uses document summaries for context awareness (prepended to message)

---

This finalized architecture ensures clean separation of concerns and efficient asset management! 🚀

Reviewing the finalized flow we documented:

[2 tools called]

## Finalized asset upload and chat flow

### Complete flow

```
┌─────────────┐
│  Frontend   │
└──────┬──────┘
       │ 1. Upload file
       ▼
┌──────────────────────────────────────────────┐
│         NestJS Backend                        │
│ ┌──────────────────────────────────────────┐ │
│ │ 2. Receive file upload                   │ │
│ │    - Validate file                       │ │
│ │    - Upload to S3 → get S3 URL           │ │
│ │    - Determine file type (image/doc)     │ │
│ └───────────────┬──────────────────────────┘ │
│                 │                             │
│                 ▼                             │
│ ┌──────────────────────────────────────────┐ │
│ │ 3. Call Python Backend                   │ │
│ │    Image? → POST /assets/process-image   │ │
│ │    Doc?   → POST /assets/process-document│ │
│ └───────────────┬──────────────────────────┘ │
└─────────────────┼─────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────┐
│      Python Backend (Processing)              │
│ ┌──────────────────────────────────────────┐ │
│ │ 4. Process File                          │ │
│ │                                          │ │
│ │ Documents:                               │ │
│ │  - Download from S3                      │ │
│ │  - Generate summary (LLM)                │ │
│ │  - Chunk document                        │ │
│ │  - Create embeddings                     │ │
│ │  - Store in MongoDB (document_embeddings)│ │
│ │                                          │ │
│ │ Images:                                  │ │
│ │  - Download from S3                      │ │
│ │  - Analyze with vision model             │ │
│ │  - Store in MongoDB (image_embeddings)   │ │
│ │                                          │ │
│ │ 5. Return structured result              │ │
│ └───────────────┬──────────────────────────┘ │
└─────────────────┼─────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────┐
│         NestJS Backend                        │
│ ┌──────────────────────────────────────────┐ │
│ │ 6. Store in Project.metadata             │ │
│ │    - Documents → metadata.documents[]    │ │
│ │    - Images    → metadata.images[]       │ │
│ │    - Update metadata.aiContext           │ │
│ └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

### Chat flow (with assets)

```
┌─────────────┐
│  Frontend   │
└──────┬──────┘
       │ Send chat message
       ▼
┌──────────────────────────────────────────────┐
│         NestJS Backend                        │
│ ┌──────────────────────────────────────────┐ │
│ │ 1. Retrieve Project.metadata             │ │
│ │    for session_id                        │ │
│ └───────────────┬──────────────────────────┘ │
│                 │                             │
│                 ▼                             │
│ ┌──────────────────────────────────────────┐ │
│ │ 2. Extract Asset Context                 │ │
│ │    - image_urls: [] from metadata.images │ │
│ │    - document_context: [] from docs      │ │
│ └───────────────┬──────────────────────────┘ │
│                 │                             │
│                 ▼                             │
│ ┌──────────────────────────────────────────┐ │
│ │ 3. Send to Python /chat with context     │ │
│ │    {                                     │ │
│ │      message: "...",                     │ │
│ │      image_urls: ["url1", "url2"],      │ │
│ │      document_context: [{...}]          │ │
│ │    }                                     │ │
│ └───────────────┬──────────────────────────┘ │
└─────────────────┼─────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────┐
│      Python Backend                           │
│ ┌──────────────────────────────────────────┐ │
│ │ 4. Build Message                         │ │
│ │    - Add document summaries to text      │ │
│ │    - Add images as multimodal input      │ │
│ │                                          │ │
│ │ 5. Process with Agent                    │ │
│ │    - Vision model sees images            │ │
│ │    - Agent aware of document summaries   │ │
│ │    - RAG search for detailed retrieval   │ │
│ └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

### Key points

1. Separation of concerns

   - Python: processing only (no DB metadata storage)
   - NestJS: business logic, S3 upload, metadata storage

2. Metadata structure

   - Separate arrays: `documents[]` and `images[]`
   - Stored in `Project.metadata` (PostgreSQL JSONB)
   - Session-based: `session_id` = `project.id`

3. Chat integration

   - NestJS extracts asset context from `Project.metadata`
   - Passes `image_urls` and `document_context` to Python
   - Python uses them in the agent message

4. Session isolation
   - All assets filtered by `session_id`
   - Complete privacy between sessions
   - RAG search scoped to session

Full details are documented in `api/asset_upload_architecture.md` (after the `======` separator).
