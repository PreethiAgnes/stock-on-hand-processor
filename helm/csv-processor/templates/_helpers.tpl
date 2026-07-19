{{- define "csv-processor.name" -}}csv-processor{{- end }}
{{- define "csv-processor.fullname" -}}{{ .Release.Name }}-{{ include "csv-processor.name" . }}{{- end }}

