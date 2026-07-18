.PHONY: test fmt vet

test:
	cd ../chaincode && go test ./...

fmt:
	cd ../chaincode && test -z "$$(gofmt -l .)"

vet:
	cd ../chaincode && go vet ./...
