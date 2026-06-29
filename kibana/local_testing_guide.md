# Local Kubernetes Testing Guide for WHMCS & Elastic APM

This guide outlines how to run a completely self-contained local testing cluster on your homelab or local K8s environment using Elasticsearch, Kibana, APM Server, MySQL, and the WHMCS application.

---

## Step 0: Build and Load the WHMCS Development Image

Since the production base images are in a private registry, you must first build a development image locally using the public Dockerfile we created and load it into your Kubernetes cluster.

### 1. Build the Development Image locally
From the root of the project, run:
```bash
docker build -f Dockerfile.development -t homelab/whmcs-app:optimize-whmcs .
```

### 2. Make the Image Available to Kubernetes
Depending on your cluster environment, choose one of these methods to load the image onto the nodes:

* **Method A (Public Registry)**: Tag and push the image to a public repository (e.g. Docker Hub):
  ```bash
  docker tag homelab/whmcs-app:optimize-whmcs your-username/whmcs-app:optimize-whmcs
  docker push your-username/whmcs-app:optimize-whmcs
  ```
  *(If you choose this, change the image name in Step 4's deployment manifest from `homelab/whmcs-app:optimize-whmcs` to `your-username/whmcs-app:optimize-whmcs`).*

* **Method B (Minikube)**: Load the image directly into your minikube cache:
  ```bash
  minikube image load homelab/whmcs-app:optimize-whmcs
  ```

* **Method C (K3s / MicroK8s containerd cache)**: Save the image to a tarball, transfer it to the node, and import it into the containerd cache:
  ```bash
  # 1. Save locally
  docker save homelab/whmcs-app:optimize-whmcs > whmcs-dev.tar
  # 2. Copy to K8s node
  scp whmcs-dev.tar user@node-ip:/tmp/
  # 3. Import on the node
  sudo ctr -n=k8s.io images import /tmp/whmcs-dev.tar
  ```

---

## Step 1: Deploy Elasticsearch and Kibana

Apply the following manifests to deploy Elasticsearch and Kibana inside a dedicated `monitoring` namespace:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: elasticsearch
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: elasticsearch
  template:
    metadata:
      labels:
        app: elasticsearch
    spec:
      containers:
        - name: elasticsearch
          image: docker.elastic.co/elasticsearch/elasticsearch:7.17.9
          ports:
            - containerPort: 9200
            - containerPort: 9300
          env:
            - name: discovery.type
              value: "single-node"
            - name: ES_JAVA_OPTS
              value: "-Xms512m -Xmx512m"
---
apiVersion: v1
kind: Service
metadata:
  name: elasticsearch
  namespace: monitoring
spec:
  ports:
    - port: 9200
  selector:
    app: elasticsearch
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kibana
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kibana
  template:
    metadata:
      labels:
        app: kibana
    spec:
      containers:
        - name: kibana
          image: docker.elastic.co/kibana/kibana:7.17.9
          ports:
            - containerPort: 5601
          env:
            - name: ELASTICSEARCH_HOSTS
              value: "http://elasticsearch:9200"
---
apiVersion: v1
kind: Service
metadata:
  name: kibana
  namespace: monitoring
spec:
  ports:
    - port: 5601
  selector:
    app: kibana
```

---

## Step 2: Deploy the APM Server

Apply this manifest to deploy the APM Server in the `monitoring` namespace, which connects to the local Elasticsearch service:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: apm-server
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: apm-server
  template:
    metadata:
      labels:
        app: apm-server
    spec:
      containers:
        - name: apm-server
          image: docker.elastic.co/apm/apm-server:7.17.9
          ports:
            - containerPort: 8200
          env:
            - name: apm-server.host
              value: "0.0.0.0:8200"
            - name: apm-server.rum.enabled
              value: "true"
            - name: output.elasticsearch.hosts
              value: "['http://elasticsearch:9200']"
---
apiVersion: v1
kind: Service
metadata:
  name: apm-server
  namespace: monitoring
spec:
  ports:
    - port: 8200
  selector:
    app: apm-server
```

---

## Step 3: Deploy a Local MySQL Database (with Persistent Storage)

Apply this manifest to deploy a single-node MySQL 5.7 database with a 5GB PersistentVolumeClaim (PVC) in the default namespace:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mysql-pvc
  namespace: default
spec:
  storageClassName: local-path
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mysql
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mysql
  template:
    metadata:
      labels:
        app: mysql
    spec:
      containers:
        - name: mysql
          image: mysql:5.7
          ports:
            - containerPort: 3306
          env:
            - name: MYSQL_ROOT_PASSWORD
              value: "root_password"
            - name: MYSQL_DATABASE
              value: "whmcs_db"
            - name: MYSQL_USER
              value: "whmcs"
            - name: MYSQL_PASSWORD
              value: "whmcs_password"
          volumeMounts:
            - name: mysql-persistent-storage
              mountPath: /var/lib/mysql
      volumes:
        - name: mysql-persistent-storage
          persistentVolumeClaim:
            claimName: mysql-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: mysql
  namespace: default
spec:
  ports:
    - port: 3306
  selector:
    app: mysql
```

---

## Step 4: Deploy the WHMCS App and Service Locally in K8s

Deploy the WHMCS application container and expose it using a `NodePort` Service on port `30080`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: whmcs-local
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: whmcs-local
  template:
    metadata:
      labels:
        app: whmcs-local
    spec:
      containers:
        - name: whmcs-app
          image: homelab/whmcs-app:optimize-whmcs
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 80
          env:
            - name: INSTRUMENT_ELASTIC_APM
              value: "True"
            - name: APM_SERVICE_NAME
              value: "local-whmcs"
            - name: APM_SERVER_URL
              value: "http://apm-server.monitoring.svc.cluster.local:8200"
            - name: DB_HOST
              value: "mysql"
            - name: DB_PORT
              value: "3306"
            - name: DB_USER
              value: "whmcs"
            - name: DB_PASSWORD
              value: "whmcs_password"
            - name: DB_NAME
              value: "whmcs_db"
            - name: WHMCS_LICENCE
              value: "YOUR_DEV_LICENSE"
---
apiVersion: v1
kind: Service
metadata:
  name: whmcs-local
  namespace: default
spec:
  type: NodePort
  ports:
    - port: 80
      targetPort: 80
      nodePort: 30080
  selector:
    app: whmcs-local
```

---

## Step 5: Verify Telemetry and Optimization in Kibana

1. **Access WHMCS**:
   You can access the WHMCS installation using one of two methods:
   - **Method A (NodePort)**: Open `http://<KUBERNETES_NODE_IP>:30080/cloud/` in your browser.
   - **Method B (Port Forward)**: If NodePort is not accessible from your machine, port-forward the service:
     ```bash
     kubectl port-forward svc/whmcs-local 8080:80
     ```
     Then open `http://localhost:8080/cloud/` in your browser.

2. **Access Kibana**:
   Port-forward to Kibana locally:
   ```bash
   kubectl port-forward svc/kibana -n monitoring 5601:5601
   ```
   Open `http://localhost:5601` in your browser.

3. **Generate Traffic**:
   Trigger requests on the local WHMCS deployment (e.g. click around the client area or administration panels at the `/cloud/` path).

4. **Check the APM Dashboard**:
   - In Kibana, go to **Observability** > **APM** > **Services**.
   - You should see `local-whmcs` active in the service list.
   - Click on the service to verify that transactions, trace waterfall graphs, and SQL queries are logged.

5. **Verify Memory Allocation and Asynchronous Offloading**:
   - Check that `USE_ZEND_ALLOC` is enabled (`USE_ZEND_ALLOC=1` by default) by accessing a simple `phpinfo.php` file inside the container, or verify that CPU utilization remains low.
   - In `phpinfo.php` (or running `php -i`), verify that `elastic_apm.async_backend_comm` displays as `true` (confirming that trace uploading is executing asynchronously in the background).
