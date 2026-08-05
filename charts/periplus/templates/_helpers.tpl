{{- define "periplus.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "periplus.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "periplus.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "periplus.labels" -}}
helm.sh/chart: {{ include "periplus.chart" . }}
app.kubernetes.io/name: {{ include "periplus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "periplus.selectorLabels" -}}
app.kubernetes.io/name: {{ include "periplus.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "periplus.componentSelectorLabels" -}}
{{ include "periplus.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "periplus.backendImage" -}}
{{- printf "%s:%s" .Values.images.backend.repository (.Values.images.backend.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "periplus.webImage" -}}
{{- printf "%s:%s" .Values.images.web.repository (.Values.images.web.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "periplus.podPlacement" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.topologySpreadConstraints }}
topologySpreadConstraints:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "periplus.backendEnv" -}}
- name: PERIPLUS_CONTROL_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.controlDatabase.name | quote }}
      key: {{ .Values.secrets.controlDatabase.urlKey | quote }}
- name: PERIPLUS_DUCKLAKE_METADATA_PATH
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.ducklakeMetadata.name | quote }}
      key: {{ .Values.secrets.ducklakeMetadata.pathKey | quote }}
- name: PERIPLUS_NATS_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.nats.name | quote }}
      key: {{ .Values.secrets.nats.urlKey | quote }}
- name: PERIPLUS_NATS_SEED
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.nats.name | quote }}
      key: {{ .Values.secrets.nats.seedKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_ENDPOINT
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.endpointKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_REGION
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.regionKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_BUCKET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.bucketKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.accessKeyIdKey | quote }}
- name: PERIPLUS_REPOSITORY_S3_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.secretAccessKeyKey | quote }}
- name: PERIPLUS_DUCKLAKE_S3_ENDPOINT
  value: {{ .Values.config.ducklake.s3Endpoint | quote }}
- name: PERIPLUS_DUCKLAKE_S3_REGION
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.regionKey | quote }}
- name: PERIPLUS_DUCKLAKE_S3_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.accessKeyIdKey | quote }}
- name: PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.s3.name | quote }}
      key: {{ .Values.secrets.s3.secretAccessKeyKey | quote }}
- name: PERIPLUS_DUCKLAKE_ALIAS
  value: {{ .Values.config.ducklake.alias | quote }}
- name: PERIPLUS_DUCKLAKE_METADATA_SCHEMA
  value: {{ .Values.config.ducklake.metadataSchema | quote }}
- name: PERIPLUS_DUCKLAKE_DATA_PATH
  value: {{ .Values.config.ducklake.dataPath | quote }}
- name: PERIPLUS_DUCKLAKE_S3_URL_STYLE
  value: {{ .Values.config.ducklake.s3UrlStyle | quote }}
- name: PERIPLUS_DUCKLAKE_S3_USE_SSL
  value: {{ .Values.config.ducklake.s3UseSSL | quote }}
- name: PERIPLUS_REPOSITORY_STORAGE
  value: s3
- name: PERIPLUS_REPOSITORY_S3_PREFIX
  value: {{ .Values.config.repository.prefix | quote }}
- name: PERIPLUS_REPOSITORY_S3_URL_STYLE
  value: {{ .Values.config.repository.s3UrlStyle | quote }}
- name: PERIPLUS_REPOSITORY_S3_USE_SSL
  value: {{ .Values.config.repository.s3UseSSL | quote }}
- name: PERIPLUS_GRAPH_STREAM_REPLICAS
  value: {{ .Values.config.nats.graphStreamReplicas | quote }}
- name: PERIPLUS_CATALOGUE_WORK_STREAM_REPLICAS
  value: {{ .Values.config.nats.catalogueWorkStreamReplicas | quote }}
- name: PERIPLUS_INGEST_RESULT_REPLICAS
  value: {{ .Values.config.nats.ingestResultReplicas | quote }}
- name: PERIPLUS_LOG_LEVEL
  value: {{ .Values.config.logLevel | quote }}
- name: PERIPLUS_AI_MODEL
  value: {{ .Values.config.aiModel | quote }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{- define "periplus.envFrom" -}}
{{- with .Values.extraEnvFrom }}
envFrom:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "periplus.backendPodSpec" -}}
automountServiceAccountToken: false
terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{ include "periplus.podPlacement" . }}
{{- end }}
