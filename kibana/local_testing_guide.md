# Local PHP Testing Guide for WHMCS & Elastic APM

Since the organization base images are in a private registry, you can test the APM agent optimizations natively on your local environment or virtual machine as a standard PHP project. 

This guide uses a hybrid approach:
1. **Elastic Stack & Database (Public Images)**: Run via a simple local Docker Compose file (no private registry login required).
2. **WHMCS & APM (Native)**: Run natively on your machine or VM using PHP, ionCube, and the APM extension.

---

## Step 1: Run the Elastic Stack & MySQL locally (No credentials required)

Create a `docker-compose.yml` file in a temporary folder or inside your project root to spin up Elasticsearch, Kibana, APM Server, and MySQL:

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

WHMCS 8.10.1 (the version in this repository) officially supports **PHP 7.4, 8.1, and 8.2** (with PHP 8.1 being the recommended and optimized version). 

You can use either **PHP 7.3/7.4** or a more modern version like **PHP 8.1**.

### 1. Install PHP (PHP 8.1 Recommended or PHP 7.3)

* **Option A: Install PHP 8.1 (Recommended)**:
  * **On Linux (Ubuntu/Debian)**:
    ```bash
    sudo apt update
    sudo apt install -y software-properties-common
    sudo add-apt-repository ppa:ondrej/php -y
    sudo apt update
    sudo apt install -y php8.1 php8.1-cli php8.1-common php8.1-mysql php8.1-xml php8.1-mbstring php8.1-zip php8.1-curl php8.1-gd php8.1-bcmath
    ```
  * **On macOS**:
    ```bash
    brew tap shivammathur/php
    brew install shivammathur/php/php@8.1
    brew link --overwrite --force php@8.1
    ```

* **Option B: Install PHP 7.3 (Legacy)**:
  * **On Linux (Ubuntu/Debian)**:
    ```bash
    sudo apt update
    sudo apt install -y software-properties-common
    sudo add-apt-repository ppa:ondrej/php -y
    sudo apt update
    sudo apt install -y php7.3 php7.3-cli php7.3-common php7.3-mysql php7.3-xml php7.3-mbstring php7.3-zip php7.3-curl php7.3-gd php7.3-json php7.3-bcmath
    ```
  * **On macOS**:
    ```bash
    brew tap shivammathur/php
    brew install shivammathur/php/php@7.3
    brew link --overwrite --force php@7.3
    ```

Verify the version is active by running `php -v`.

### 2. Install ionCube Loader

WHMCS files are compiled/encrypted using ionCube. You must use the loader version matching your PHP version:

* **If using PHP 7.3**:
  * **On Linux**: Use the included file `/path/to/tcloud-whmcs/ioncube_loader_lin_7.3.so`.
  * **On macOS**: Download the OS X 64-bit PHP 7.3 loader from [ionCube](https://www.ioncube.com/loaders.php).
* **If using PHP 8.1**:
  * **On Linux**: Download the Linux 64-bit PHP 8.1 loader (`ioncube_loader_lin_8.1.so`) from [ionCube](https://www.ioncube.com/loaders.php).
  * **On macOS**: Download the OS X 64-bit PHP 8.1 loader (`ioncube_loader_dar_8.1.so`) from [ionCube](https://www.ioncube.com/loaders.php).

Add the extension path to the top of your active `php.ini`:
```ini
zend_extension = "/path/to/ioncube_loader_xxxx_x.x.so"
```

### 3. Install the Elastic APM PHP Agent
* **On Linux (Ubuntu/Debian)**:
  Install the pre-downloaded package from the repository:
  ```bash
  sudo dpkg -i apm-agent-php_1.17.0_amd64.deb
  ```
* **On macOS**:
  Download the Darwin tarball release of the APM Agent from the [Elastic APM PHP releases page](https://github.com/elastic/apm-agent-php/releases/tag/v1.17.0) (e.g. `apm-agent-php-1.17.0-darwin-x86_64.tar.gz`), unpack it, and configure the path in your `php.ini`.

### 4. Verify APM & OPcache inside the repository's `php.ini`

Since you are running the project using the repository's own `php.ini` file (`php -c php.ini`), **you do not need to modify your system's global php.ini**. 

The repository's [php.ini](file:///Users/gedeon/Dev/work/tcloud-whmcs/php.ini) is already pre-configured at the bottom with:
```ini
elastic_apm.async_backend_comm=true
elastic_apm.transaction_sample_rate=0.5
elastic_apm.span_stack_trace_min_duration=50ms
```

You only need to ensure the following are configured in it:
1. **APM Server URL**: Update `elastic_apm.server_url` to point to your local compose stack:
   ```ini
   elastic_apm.server_url="http://localhost:8200"
   ```
2. **APM Service Name**: Update `elastic_apm.service_name` to your desired test identifier:
   ```ini
   elastic_apm.service_name="local-whmcs-native"
   ```
3. **Extensions**: Ensure `extension=elastic_apm.so` is appended to the bottom, and your `zend_extension="/path/to/ioncube..."` path is added at the top.

> [!TIP]
> If you wish to enable OPcache locally to align with the production/staging performance profiles, ensure the OPcache directives are uncommented in the local `php.ini`. Keep `opcache.validate_timestamps=1` set during testing so code changes are visible immediately without process restarts.

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

### 2. Start the Local PHP Development Server (Using the Project's `php.ini`)

You can tell PHP to use the `php.ini` file located inside the repository directly instead of your global system `php.ini` by using the `-c` flag:

1. First, open the repository's [php.ini](file:///Users/gedeon/Dev/work/tcloud-whmcs/php.ini) and replace the `$APM_SERVICE_NAME` placeholder at the bottom with a descriptive name (e.g., `local-whmcs-native`), and change `https://apm.jisort.com` to `http://localhost:8200`.
2. Add your `zend_extension="/path/to/ioncube..."` line at the very top of the repository's `php.ini`.
3. If the Elastic APM agent is not installed globally on your machine, add `extension=elastic_apm.so` at the bottom of the repository's `php.ini` as well.
4. Run the PHP development server, pointing to the local `php.ini`:

```bash
php -c php.ini -S localhost:8000
```

---

## Step 4: Verify Telemetry in Kibana

1. **Access WHMCS**:
   Open `http://localhost:8000/` or `http://localhost:8000/cloud/` in your browser.
2. **Access Kibana**:
   Open `http://localhost:5601` in your browser.
3. Go to **Observability** > **APM** > **Services**.
4. You should see `local-whmcs-native` listed. Click on it to check that transactions are logged asynchronously without blocking the PHP process.
