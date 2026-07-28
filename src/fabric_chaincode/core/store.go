package core

type Caller struct {
	ClientID   string            `json:"clientId"`
	MSPID      string            `json:"mspId"`
	Attributes map[string]string `json:"attributes"`
}

type Tx interface {
	Get(key string, out any) (bool, error)
	Put(key string, value any) error
	Delete(key string) error
	Caller() Caller
	TimestampUnix() int64
}
