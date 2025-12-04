# Chat Route Asset Context Flow

## Overview

This document explains how asset context (uploaded documents and images) is integrated into the chat route for context-aware conversations.

---

## **Fields Added to MessageRequest**

### **New Fields**

```python
class DocumentContext(BaseModel):
    """Document context for chat requests"""
    filename: str      # e.g., "script.py"
    type: str         # File type/extension, e.g., "py", "pdf"
    summary: str      # Document summary from processing

class MessageRequest(BaseModel):
    # ... existing fields ...
    
    # NEW: Asset context (provided by NestJS from Project.metadata)
    image_urls: Optional[List[str]] = None  
    # Array of image URLs for vision model input
    
    document_context: Optional[List[DocumentContext]] = None  
    # Array of document summaries for context awareness
```

---

## **Complete Flow Diagram**

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. User sends chat message through Frontend                     │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. NestJS Backend receives chat request                         │
│                                                                  │
│    - Retrieves Project.metadata for session_id                  │
│    - Extracts assets:                                           │
│      • image_urls from Project.metadata.images[]                │
│      • document_context from Project.metadata.documents[]       │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. NestJS sends to Python /chat endpoint                        │
│                                                                  │
│    POST /chat                                                   │
│    {                                                            │
│      "message": "Create a dashboard based on my image",         │
│      "session_id": "project_123",                               │
│      "user_id": "user_456",                                     │
│      "image_urls": [                                            │
│        "https://.../dashboard.png",                             │
│        "https://.../mockup.jpg"                                 │
│      ],                                                          │
│      "document_context": [                                      │
│        {                                                         │
│          "filename": "script.py",                               │
│          "type": "py",                                          │
│          "summary": "Python script for file operations..."      │
│        },                                                        │
│        {                                                         │
│          "filename": "api.md",                                  │
│          "type": "md",                                          │
│          "summary": "API documentation with endpoints..."        │
│        }                                                         │
│      ]                                                           │
│    }                                                            │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Python Backend processes asset context                       │
│                                                                  │
│    Step 4a: Build document context (prepended to message)       │
│    ──────────────────────────────────────────────────────────   │
│    [Uploaded Documents Context]                                 │
│    - script.py (py): Python script for file operations...       │
│    - api.md (md): API documentation with endpoints...            │
│                                                                  │
│    Step 4b: Build multimodal message                            │
│    ──────────────────────────────────────────────────────────   │
│    message_parts = [                                            │
│      {                                                           │
│        "type": "text",                                          │
│        "text": "[Document Context]\n\nUser message..."          │
│      },                                                          │
│      {                                                           │
│        "type": "image_url",                                     │
│        "image_url": {"url": "https://.../dashboard.png"}        │
│      },                                                          │
│      {                                                           │
│        "type": "image_url",                                     │
│        "image_url": {"url": "https://.../mockup.jpg"}           │
│      }                                                           │
│    ]                                                            │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Agent processes with full context                            │
│                                                                  │
│    - Sees all uploaded documents (summaries)                    │
│    - Sees all uploaded images (vision input)                    │
│    - Can use RAG search for detailed document retrieval         │
│    - Responds with context-aware output                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## **How Context is Ingested**

### **1. Document Context Integration**

Documents are **prepended** to the user's message as text context:

```python
# Original user message
message = "Create a dashboard based on my uploaded code"

# After adding document context:
"""
[Uploaded Documents Context]
- script.py (py): Python script for file operations with E2B sandbox integration...
- api.md (md): API documentation describing REST endpoints and authentication...

Create a dashboard based on my uploaded code
"""
```

**Implementation:**
```python
if request.document_context:
    doc_context_lines = ["\n[Uploaded Documents Context]"]
    for doc in request.document_context:  # ✅ Handles multiple docs
        doc_context_lines.append(
            f"- {doc.filename} ({doc.type}): {doc.summary[:200]}..."
        )
    message_content = "\n".join(doc_context_lines) + "\n\n" + message_content
```

**Key Points:**
- ✅ **Multiple documents supported** - Loops through all documents
- ✅ **Prepended to message** - Agent sees context before user query
- ✅ **Summary format** - `filename (type): summary...`
- ⚠️ **Summary truncated to 200 chars** - To avoid token limits

---

### **2. Image Context Integration**

Images are added as **multimodal content parts** for vision models:

```python
# If images are present, create multimodal message
if request.image_urls:
    message_parts = [
        {
            "type": "text",
            "text": "[Document Context]\n\nUser message..."
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://.../dashboard.png"}  # Image 1
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://.../mockup.jpg"}     # Image 2
        }
        # ✅ Can have multiple images
    ]
    human_message = HumanMessage(content=message_parts)
```

**Implementation:**
```python
if request.image_urls:
    message_parts = [{"type": "text", "text": message_content}]
    for image_url in request.image_urls:  # ✅ Handles multiple images
        message_parts.append({
            "type": "image_url",
            "image_url": {"url": image_url}
        })
    human_message = HumanMessage(content=message_parts)
```

**Key Points:**
- ✅ **Multiple images supported** - Loops through all image URLs
- ✅ **Multimodal format** - Vision models can process images
- ✅ **Order preserved** - Images added in array order
- ✅ **Combined with text** - Text + all images in single message

---

## **Examples**

### **Example 1: Multiple Documents Only**

**Request:**
```json
{
  "message": "What patterns do you see in my code?",
  "session_id": "project_123",
  "document_context": [
    {
      "filename": "auth.js",
      "type": "js",
      "summary": "JWT authentication middleware with role-based access control..."
    },
    {
      "filename": "user-service.py",
      "type": "py",
      "summary": "User management service with CRUD operations and validation..."
    }
  ]
}
```

**Message sent to Agent:**
```
[Uploaded Documents Context]
- auth.js (js): JWT authentication middleware with role-based access control...
- user-service.py (py): User management service with CRUD operations and validation...

What patterns do you see in my code?
```

---

### **Example 2: Multiple Images Only**

**Request:**
```json
{
  "message": "Create a UI matching this design",
  "session_id": "project_123",
  "image_urls": [
    "https://s3.../dashboard.png",
    "https://s3.../mobile-view.png",
    "https://s3.../color-palette.png"
  ]
}
```

**Message sent to Agent:**
```
Multimodal Message:
- Text: "Create a UI matching this design"
- Image 1: dashboard.png
- Image 2: mobile-view.png
- Image 3: color-palette.png
```

---

### **Example 3: Both Documents and Images**

**Request:**
```json
{
  "message": "Build a dashboard based on my designs and API docs",
  "session_id": "project_123",
  "image_urls": [
    "https://s3.../dashboard-design.png",
    "https://s3.../components.png"
  ],
  "document_context": [
    {
      "filename": "api-spec.yaml",
      "type": "yaml",
      "summary": "OpenAPI specification for dashboard endpoints..."
    },
    {
      "filename": "README.md",
      "type": "md",
      "summary": "Project documentation with setup instructions..."
    }
  ]
}
```

**Message sent to Agent:**
```
Multimodal Message:

Text Part:
"""
[Uploaded Documents Context]
- api-spec.yaml (yaml): OpenAPI specification for dashboard endpoints...
- README.md (md): Project documentation with setup instructions...

Build a dashboard based on my designs and API docs
"""

Image Parts:
- dashboard-design.png
- components.png
```

---

## **Multiple Assets Handling**

### **✅ Multiple Documents**

- **Array Support**: `document_context` is `List[DocumentContext]`
- **Iteration**: Loops through all documents
- **Format**: Each doc formatted as `filename (type): summary`
- **Order**: Preserved as provided in array

**Code:**
```python
for doc in request.document_context:  # ✅ Handles all docs
    doc_context_lines.append(f"- {doc.filename} ({doc.type}): {doc.summary[:200]}...")
```

---

### **✅ Multiple Images**

- **Array Support**: `image_urls` is `List[str]`
- **Iteration**: Loops through all image URLs
- **Format**: Each image as `{"type": "image_url", "image_url": {"url": "..."}}`
- **Order**: Preserved as provided in array

**Code:**
```python
for image_url in request.image_urls:  # ✅ Handles all images
    message_parts.append({
        "type": "image_url",
        "image_url": {"url": image_url}
    })
```

---

### **✅ Combined (Documents + Images)**

- **Both arrays processed independently**
- **Documents → Text context** (prepended)
- **Images → Multimodal parts** (appended)
- **Works together seamlessly**

---

## **Limitations & Considerations**

### **1. Document Summary Truncation**
- Summaries truncated to **200 characters** to manage token limits
- Full summaries available via RAG search if needed
- Consider making truncation configurable

### **2. Image Count Limits**
- Vision models may have limits on number of images per message
- Current implementation sends all images (no limit check)
- Consider adding configurable limit (e.g., max 5 images)

### **3. Token Budget**
- Document context adds to token count
- Multiple long summaries could exhaust context window
- Full summaries stored in RAG for detailed retrieval

### **4. Image Size/Format**
- No validation of image URLs or formats
- Depends on S3 URLs being valid and accessible
- Consider adding validation/error handling

---

## **Improvements (Future)**

1. **Configurable truncation** - Make summary length configurable
2. **Image limit** - Add max image count validation
3. **Token counting** - Track context token usage
4. **Lazy loading** - Only include relevant assets per query
5. **Asset selection** - Allow filtering which assets to include
6. **Priority system** - Most recent/relevant assets first

---

## **Summary**

✅ **Fields Added:**
- `image_urls: Optional[List[str]]` - Multiple image URLs
- `document_context: Optional[List[DocumentContext]]` - Multiple document summaries

✅ **Context Integration:**
- Documents → Prepended as text context
- Images → Added as multimodal parts

✅ **Multiple Assets:**
- ✅ Multiple documents handled via array iteration
- ✅ Multiple images handled via array iteration
- ✅ Both work together seamlessly

✅ **Session Isolation:**
- All assets filtered by `session_id`
- NestJS retrieves only assets for current session
- Complete privacy between sessions

