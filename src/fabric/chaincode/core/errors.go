package core

import "fmt"

// CodeError exposes a stable machine-readable contract error code.
type CodeError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (e CodeError) Error() string {
	if e.Message == "" {
		return e.Code
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}
