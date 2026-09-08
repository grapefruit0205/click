package service

import (
	"encoding/json"
	"os"
	"testing"
)

func TestSharedSchemaVersion(t *testing.T) {
	content, err := os.ReadFile("../../shared/api.json")
	if err != nil {
		t.Fatal(err)
	}
	var schema struct{ Version int `json:"version"` }
	if err := json.Unmarshal(content, &schema); err != nil {
		t.Fatal(err)
	}
	if schema.Version != 1 {
		t.Fatalf("unexpected schema version: %d", schema.Version)
	}
}
