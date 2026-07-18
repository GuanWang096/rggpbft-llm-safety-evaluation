package internal

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"strings"
	"time"
)

const DefaultAPI = "http://localhost:5001"

type Client struct {
	API  string
	HTTP *http.Client
}

func NewClient(api string) *Client {
	if api == "" {
		api = DefaultAPI
	}
	return &Client{API: strings.TrimRight(api, "/"), HTTP: &http.Client{Timeout: 0}}
}

// NewClientWithTimeout creates a client with a request timeout.
func NewClientWithTimeout(api string, timeout time.Duration) *Client {
	c := NewClient(api)
	c.HTTP.Timeout = timeout
	return c
}

type AddResult struct {
	CID        string `json:"cid"`
	SHA256     string `json:"sha256"`
	ByteLength int64  `json:"byteLength"`
}

type addResponse struct {
	Name string `json:"Name"`
	Hash string `json:"Hash"`
	Size string `json:"Size"`
}

func (c *Client) Add(data []byte, filename string) (AddResult, error) {
	if len(data) == 0 {
		return AddResult{}, fmt.Errorf("cannot add empty data")
	}
	if filename == "" {
		filename = "evidence.bin"
	}

	h := sha256.Sum256(data)
	sha256Hex := hex.EncodeToString(h[:])

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		return AddResult{}, fmt.Errorf("create form part: %w", err)
	}
	if _, err := part.Write(data); err != nil {
		return AddResult{}, fmt.Errorf("write form part: %w", err)
	}
	writer.Close()

	req, err := http.NewRequest("POST", c.API+"/api/v0/add", &body)
	if err != nil {
		return AddResult{}, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return AddResult{}, fmt.Errorf("ipfs add request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return AddResult{}, fmt.Errorf("ipfs add returned %d", resp.StatusCode)
	}

	var addResp addResponse
	if err := json.NewDecoder(resp.Body).Decode(&addResp); err != nil {
		return AddResult{}, fmt.Errorf("decode add response: %w", err)
	}
	if strings.TrimSpace(addResp.Hash) == "" {
		return AddResult{}, fmt.Errorf("ipfs returned empty CID")
	}

	return AddResult{
		CID:        addResp.Hash,
		SHA256:     sha256Hex,
		ByteLength: int64(len(data)),
	}, nil
}

func (c *Client) Cat(cid string) ([]byte, error) {
	if strings.TrimSpace(cid) == "" {
		return nil, fmt.Errorf("CID is required")
	}

	req, err := http.NewRequest("POST", c.API+"/api/v0/cat?arg="+cid, nil)
	if err != nil {
		return nil, fmt.Errorf("create cat request: %w", err)
	}

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("ipfs cat request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ipfs cat returned %d for %s", resp.StatusCode, cid)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read cat response: %w", err)
	}
	return data, nil
}

func (c *Client) Verify(cid string, expectedSHA256 string, expectedLength int64) error {
	data, err := c.Cat(cid)
	if err != nil {
		return fmt.Errorf("verify retrieval: %w", err)
	}

	if int64(len(data)) != expectedLength {
		return fmt.Errorf("length mismatch: got %d, expected %d", len(data), expectedLength)
	}

	h := sha256.Sum256(data)
	got := hex.EncodeToString(h[:])
	if got != expectedSHA256 {
		return fmt.Errorf("sha256 mismatch: got %s, expected %s", got, expectedSHA256)
	}

	return nil
}
