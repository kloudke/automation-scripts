# Native PHP Testing Guide for WHMCS & Elastic APM

Since the organization base images are in a private registry, you can test the APM agent optimizations natively on a local environment or virtual machine as a standard PHP project. 

This guide uses a hybrid approach:
1. **Elastic Stack (Public Images)**: Run via a simple local Docker Compose file (no private registry login required).
2. **WHMCS & APM (Native)**: Run natively on your machine or VM using PHP, ionCube, and the APM extension.

---

## Step 1: Run the Elastic Stack locally (No credentials required)

Create a `docker-compose.yml` file in a temporary folder or inside your project root to spin up Elasticsearch, Kibana, and APM Server:

```yaml
version: '3.7'
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:7.17.9
    container_name: elasticsearch
    environment:
      - discovery.type=single-node
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
    ports:
      - "9200:9200"

  kibana:
    image: docker.elastic.co/kibana/kibana:7.17.9
    container_name: kibana
    environment:
      - ELASTICSEARCH_HOSTS=http://elasticsearch:9200
    ports:
      - "5601:5601"
    depends_on:
      - elasticsearch

  apm-server:
    image: docker.elastic.co/apm/apm-server:7.17.9
    container_name: apm-server
    command: >
      apm-server -e
      -E apm-server.host=0.0.0.0:8200
      -E apm-server.rum.enabled=true
      -E output.elasticsearch.hosts=["elasticsearch:9200"]
    ports:
      - "8200:8200"
    depends_on:
      - elasticsearch

  mysql:
    image: mysql:5.7
    container_name: mysql
    ports:
      - "3306:3306"
    environment:
      - MYSQL_ROOT_PASSWORD=root_password
      - MYSQL_DATABASE=whmcs_db
      - MYSQL_USER=whmcs
      - MYSQL_PASSWORD=whmcs_password
    volumes:
      - mysql-data:/var/lib/mysql

volumes:
  mysql-data:
```

Start the stack:
```bash
docker-compose up -d
```

---

## Step 2: Configure Your Local PHP Environment

WHMCS requires **PHP 7.3 or 7.4**, the **ionCube Loader** extension (for decryption), and the **Elastic APM PHP extension**.

### 1. Install ionCube Loader
* **On Linux (Ubuntu/Debian)**: 
  The repository already includes the Linux ionCube loader: `ioncube_loader_lin_7.3.so`. Copy it to your PHP extension directory or reference it directly in your `php.ini`:
  ```ini
  zend_extension = "/path/to/tcloud-whmcs/ioncube_loader_lin_7.3.so"
  ```
* **On macOS**: 
  Download the OS X 64-bit ionCube Loader from the [official ionCube website](https://www.ioncube.com/loaders.php) and add it to your `php.ini`:
  ```ini
  zend_extension = "/path/to/ioncube_loader_dar_7.3.so"
  ```

### 2. Install the Elastic APM PHP Agent
* **On Linux (Ubuntu/Debian)**:
  Install the pre-downloaded package from the repository:
  ```bash
  sudo dpkg -i apm-agent-php_1.17.0_amd64.deb
  ```
* **On macOS**:
  Download the Darwin tarball release of the APM Agent from the [Elastic APM PHP releases page](https://github.com/elastic/apm-agent-php/releases/tag/v1.17.0) (e.g. `apm-agent-php-1.17.0-darwin-x86_64.tar.gz`), unpack it, and configure the path in your `php.ini`.

### 3. Append APM Configuration to `php.ini`
Locate your active `php.ini` (run `php --ini` to find it) and append the following configuration:

```ini
extension=elastic_apm.so

elastic_apm.enabled=true
elastic_apm.server_url="http://localhost:8200"
elastic_apm.service_name="local-whmcs-native"
elastic_apm.async_backend_comm=true
elastic_apm.transaction_sample_rate=0.5
elastic_apm.span_stack_trace_min_duration=50ms
```

> [!IMPORTANT]
> Make sure `elastic_apm.async_backend_comm=true` is present. This is the crucial setting we implemented to solve the request blocking and latency spikes!

---

## Step 3: Configure and Start the WHMCS Application

### 1. Point to Your Local Database
Open `configuration.php` in the root of the project and update the credentials to match your MySQL Docker container (since MySQL exposes port `3306` to the host, you can connect using `127.0.0.1`):

```php
$db_host = '127.0.0.1'; 
$db_port = '3306';
$db_username = 'whmcs';
$db_password = 'whmcs_password';
$db_name = 'whmcs_db';
```

### 2. Start the Local PHP Development Server
From the root of the project, run:

```bash
php -S localhost:8000
```

---

## Step 4: Verify Telemetry in Kibana

1. **Access WHMCS**:
   Open `http://localhost:8000/` or `http://localhost:8000/cloud/` in your browser.
2. **Access Kibana**:
   Open `http://localhost:5601` in your browser.
3. Go to **Observability** > **APM** > **Services**.
4. You should see `local-whmcs-native` listed. Click on it to check that transactions are logged asynchronously without blocking the PHP process.
