module wow-research/agent-wow-sdk-probe

go 1.27.1

require github.com/agent-wow/agent-wow v0.0.0

require (
	go.yaml.in/yaml/v3 v3.0.4 // indirect
	golang.org/x/net v0.48.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
	golang.org/x/text v0.32.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20251202230838-ff82c1b0f217 // indirect
	google.golang.org/grpc v1.79.3 // indirect
	google.golang.org/protobuf v1.36.10 // indirect
)

replace github.com/agent-wow/agent-wow => ../../.cache/agent-wow-upstream/agent-wow
